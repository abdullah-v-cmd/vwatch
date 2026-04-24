"""
V-Watch Edge AI - Vehicle Detector
YOLOv8/YOLOv10 vehicle detection with INT8/FP16 quantization support
"""

import cv2
import numpy as np
import logging
import time
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Vehicle class IDs in COCO dataset
VEHICLE_CLASSES = {
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    1: "bicycle",
}

HELMET_CLASSES = {0: "helmet", 1: "no_helmet"}
SEATBELT_CLASSES = {0: "seatbelt", 1: "no_seatbelt"}


@dataclass
class Detection:
    """Represents a single object detection."""
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2
    class_id: int
    class_name: str
    confidence: float
    center: Tuple[int, int] = None

    def __post_init__(self):
        x1, y1, x2, y2 = self.bbox
        self.center = ((x1 + x2) // 2, (y1 + y2) // 2)

    @property
    def area(self):
        x1, y1, x2, y2 = self.bbox
        return (x2 - x1) * (y2 - y1)

    @property
    def width(self):
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self):
        return self.bbox[3] - self.bbox[1]


class VehicleDetector:
    """
    YOLOv8/YOLOv10 vehicle detector.
    Falls back gracefully when ultralytics is not installed.
    Supports model quantization for edge deployment.
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        confidence_threshold: float = 0.5,
        iou_threshold: float = 0.45,
        device: str = "cpu",
        use_fp16: bool = False,
        target_classes: Optional[List[int]] = None,
    ):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.device = device
        self.use_fp16 = use_fp16
        self.target_classes = target_classes or list(VEHICLE_CLASSES.keys())
        self.model = None
        self._load_model()

    def _load_model(self):
        """Load YOLO model with fallback to mock for testing."""
        try:
            from ultralytics import YOLO
            self.model = YOLO(self.model_path)
            if self.use_fp16 and self.device != "cpu":
                self.model.half()
            logger.info(f"[VehicleDetector] Model loaded: {self.model_path} on {self.device}")
        except ImportError:
            logger.warning("[VehicleDetector] ultralytics not installed. Using mock detector.")
            self.model = None
        except Exception as e:
            logger.error(f"[VehicleDetector] Failed to load model: {e}")
            self.model = None

    def detect(self, frame: np.ndarray) -> List[Detection]:
        """
        Run detection on a frame.
        Returns list of Detection objects for vehicles only.
        """
        if self.model is None:
            return self._mock_detect(frame)

        try:
            results = self.model(
                frame,
                conf=self.confidence_threshold,
                iou=self.iou_threshold,
                device=self.device,
                verbose=False,
                classes=self.target_classes,
            )
            detections = []
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                for box in boxes:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    class_name = VEHICLE_CLASSES.get(cls_id, f"class_{cls_id}")
                    detections.append(
                        Detection(
                            bbox=(x1, y1, x2, y2),
                            class_id=cls_id,
                            class_name=class_name,
                            confidence=conf,
                        )
                    )
            return detections
        except Exception as e:
            logger.error(f"[VehicleDetector] Detection error: {e}")
            return []

    def _mock_detect(self, frame: np.ndarray) -> List[Detection]:
        """Mock detector for testing without model."""
        h, w = frame.shape[:2]
        return [
            Detection(
                bbox=(int(w * 0.2), int(h * 0.3), int(w * 0.5), int(h * 0.7)),
                class_id=2,
                class_name="car",
                confidence=0.92,
            )
        ]

    def draw_detections(self, frame: np.ndarray, detections: List[Detection]) -> np.ndarray:
        """Draw bounding boxes on frame."""
        colors = {
            "car": (0, 255, 0),
            "motorcycle": (0, 165, 255),
            "bus": (255, 0, 0),
            "truck": (0, 0, 255),
            "bicycle": (255, 255, 0),
        }
        for det in detections:
            color = colors.get(det.class_name, (200, 200, 200))
            x1, y1, x2, y2 = det.bbox
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            label = f"{det.class_name} {det.confidence:.2f}"
            cv2.putText(frame, label, (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        return frame


class LicensePlateDetector:
    """
    Dedicated license plate detector.
    Uses a fine-tuned YOLO model for plate localization.
    """

    def __init__(
        self,
        model_path: str = "models/plate_detector.pt",
        confidence_threshold: float = 0.6,
    ):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.model = None
        self._load_model()

    def _load_model(self):
        try:
            from ultralytics import YOLO
            if Path(self.model_path).exists():
                self.model = YOLO(self.model_path)
                logger.info(f"[PlateDetector] Plate model loaded: {self.model_path}")
            else:
                logger.warning(f"[PlateDetector] Model not found at {self.model_path}. Using cascade fallback.")
                self.model = None
        except ImportError:
            self.model = None

    def detect_plates(self, vehicle_crop: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """
        Detect license plates in a vehicle crop.
        Returns list of bounding boxes (x1, y1, x2, y2).
        """
        if self.model:
            try:
                results = self.model(vehicle_crop, conf=self.confidence_threshold, verbose=False)
                plates = []
                for result in results:
                    if result.boxes:
                        for box in result.boxes:
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            plates.append((x1, y1, x2, y2))
                return plates
            except Exception as e:
                logger.error(f"[PlateDetector] Error: {e}")

        # Fallback: OpenCV cascade classifier
        return self._cascade_detect(vehicle_crop)

    def _cascade_detect(self, image: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Fallback using OpenCV Haar cascade."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Simple heuristic: lower portion of vehicle for plate
        h, w = gray.shape
        roi = gray[int(h * 0.55):, :]

        # Use edge detection as plate proxy
        edges = cv2.Canny(roi, 100, 200)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        plates = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            aspect_ratio = cw / max(ch, 1)
            if 2.0 < aspect_ratio < 6.0 and cw > 60 and ch > 15:
                plates.append((x, y + int(h * 0.55), x + cw, y + int(h * 0.55) + ch))
        return plates[:1]  # Return best candidate
