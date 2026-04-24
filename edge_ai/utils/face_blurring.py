"""
V-Watch Edge AI - Privacy Protection
Face blurring for GDPR/privacy compliance
"""

import cv2
import numpy as np
import logging
from typing import List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class FaceBlurrer:
    """
    Detects and blurs faces in frames for privacy compliance.
    Uses OpenCV DNN for robust face detection.
    """

    def __init__(
        self,
        blur_strength: int = 35,
        min_face_size: Tuple[int, int] = (30, 30),
        confidence_threshold: float = 0.7,
    ):
        self.blur_strength = blur_strength | 1  # Must be odd
        self.min_face_size = min_face_size
        self.confidence_threshold = confidence_threshold
        self._detector = None
        self._init_detector()

    def _init_detector(self):
        """Initialize face detector with DNN model or fallback to Haar cascade."""
        # Try DNN-based detector first (more accurate)
        prototxt = Path("models/deploy.prototxt")
        caffemodel = Path("models/res10_300x300_ssd_iter_140000.caffemodel")

        if prototxt.exists() and caffemodel.exists():
            try:
                self._detector = cv2.dnn.readNetFromCaffe(str(prototxt), str(caffemodel))
                self._mode = "dnn"
                logger.info("[FaceBlurrer] DNN face detector loaded.")
                return
            except Exception as e:
                logger.warning(f"[FaceBlurrer] DNN load failed: {e}")

        # Fallback: Haar cascade
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._detector = cv2.CascadeClassifier(cascade_path)
        if self._detector.empty():
            self._detector = None
            logger.warning("[FaceBlurrer] No face detector available.")
        else:
            self._mode = "haar"
            logger.info("[FaceBlurrer] Haar cascade face detector loaded.")

    def detect_faces(self, frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        """Detect faces in frame. Returns list of (x, y, w, h) bounding boxes."""
        if self._detector is None:
            return []

        if self._mode == "dnn":
            return self._dnn_detect(frame)
        return self._haar_detect(frame)

    def _dnn_detect(self, frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)), 1.0, (300, 300),
            (104.0, 177.0, 123.0)
        )
        self._detector.setInput(blob)
        detections = self._detector.forward()
        faces = []
        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]
            if confidence < self.confidence_threshold:
                continue
            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            x1, y1, x2, y2 = box.astype(int)
            fw, fh = x2 - x1, y2 - y1
            if fw >= self.min_face_size[0] and fh >= self.min_face_size[1]:
                faces.append((x1, y1, fw, fh))
        return faces

    def _haar_detect(self, frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._detector.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=self.min_face_size,
        )
        return list(faces) if len(faces) > 0 else []

    def blur_faces(self, frame: np.ndarray) -> np.ndarray:
        """Apply Gaussian blur to all detected faces in frame."""
        faces = self.detect_faces(frame)
        for (x, y, w, h) in faces:
            # Add margin around face
            margin = int(max(w, h) * 0.1)
            x1 = max(0, x - margin)
            y1 = max(0, y - margin)
            x2 = min(frame.shape[1], x + w + margin)
            y2 = min(frame.shape[0], y + h + margin)
            roi = frame[y1:y2, x1:x2]
            if roi.size > 0:
                blurred = cv2.GaussianBlur(roi, (self.blur_strength, self.blur_strength), 0)
                frame[y1:y2, x1:x2] = blurred
        return frame

    def pixelate_faces(self, frame: np.ndarray, pixel_size: int = 15) -> np.ndarray:
        """Pixelate faces as alternative to blurring."""
        faces = self.detect_faces(frame)
        for (x, y, w, h) in faces:
            roi = frame[y:y+h, x:x+w]
            if roi.size == 0:
                continue
            temp = cv2.resize(roi, (max(1, w // pixel_size), max(1, h // pixel_size)),
                              interpolation=cv2.INTER_LINEAR)
            frame[y:y+h, x:x+w] = cv2.resize(temp, (w, h), interpolation=cv2.INTER_NEAREST)
        return frame
