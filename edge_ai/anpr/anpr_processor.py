"""
V-Watch Edge AI - ANPR (Automatic Number Plate Recognition) Processor
License plate detection + OCR pipeline
"""

import cv2
import numpy as np
import re
import logging
import time
from typing import Optional, Tuple, List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)


class ImageEnhancer:
    """Image pre-processing pipeline for better OCR accuracy."""

    @staticmethod
    def enhance_plate(image: np.ndarray) -> np.ndarray:
        """Apply image enhancement for plate OCR."""
        if image is None or image.size == 0:
            return image

        # Resize to standard size
        h, w = image.shape[:2]
        if w < 120:
            scale = 120 / w
            image = cv2.resize(image, (int(w * scale), int(h * scale)),
                               interpolation=cv2.INTER_CUBIC)

        # Convert to grayscale
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

        # Denoise
        gray = cv2.fastNlMeansDenoising(gray, h=10)

        # Adaptive thresholding
        binary = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        # Morphological operations to clean up
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        # Back to 3-channel for downstream compatibility
        return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)

    @staticmethod
    def deskew(image: np.ndarray) -> np.ndarray:
        """Correct plate skew using affine transform."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
        coords = np.column_stack(np.where(gray > 0))
        if len(coords) < 5:
            return image
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        h, w = image.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        return cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC,
                              borderMode=cv2.BORDER_REPLICATE)


class OCREngine:
    """
    Multi-engine OCR with priority: EasyOCR > Tesseract > Mock
    """

    def __init__(self, languages: List[str] = None):
        self.languages = languages or ["en"]
        self._easy_reader = None
        self._use_tesseract = False
        self._init_engines()

    def _init_engines(self):
        try:
            import easyocr
            self._easy_reader = easyocr.Reader(self.languages, gpu=False, verbose=False)
            logger.info("[OCR] EasyOCR initialized.")
            return
        except ImportError:
            logger.warning("[OCR] EasyOCR not available.")

        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            self._use_tesseract = True
            logger.info("[OCR] Tesseract initialized.")
        except Exception:
            logger.warning("[OCR] Tesseract not available. Using mock OCR.")

    def read(self, image: np.ndarray) -> Tuple[str, float]:
        """
        Read text from image.
        Returns (text, confidence).
        """
        if self._easy_reader:
            return self._easy_ocr(image)
        if self._use_tesseract:
            return self._tesseract_ocr(image)
        return self._mock_ocr(image)

    def _easy_ocr(self, image: np.ndarray) -> Tuple[str, float]:
        try:
            results = self._easy_reader.readtext(image, detail=1)
            if not results:
                return "", 0.0
            # Combine all detected text
            texts = [(r[1], r[2]) for r in results if r[2] > 0.3]
            if not texts:
                return "", 0.0
            combined = " ".join(t for t, _ in texts)
            avg_conf = sum(c for _, c in texts) / len(texts)
            return combined, avg_conf
        except Exception as e:
            logger.error(f"[OCR] EasyOCR error: {e}")
            return "", 0.0

    def _tesseract_ocr(self, image: np.ndarray) -> Tuple[str, float]:
        try:
            import pytesseract
            config = "--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            data = pytesseract.image_to_data(image, config=config,
                                             output_type=pytesseract.Output.DICT)
            texts = []
            confs = []
            for i, word in enumerate(data["text"]):
                conf = int(data["conf"][i])
                if conf > 30 and word.strip():
                    texts.append(word.strip())
                    confs.append(conf / 100.0)
            if not texts:
                return "", 0.0
            return " ".join(texts), sum(confs) / len(confs)
        except Exception as e:
            logger.error(f"[OCR] Tesseract error: {e}")
            return "", 0.0

    def _mock_ocr(self, image: np.ndarray) -> Tuple[str, float]:
        """Mock OCR for testing."""
        plates = ["ABC 1234", "XYZ 5678", "DEF 9012", "GHI 3456"]
        import random
        return random.choice(plates), 0.85


class PlateTextValidator:
    """Validates and normalizes license plate text."""

    # Common plate patterns (configurable)
    PATTERNS = [
        r'^[A-Z]{2,3}\s?\d{3,4}[A-Z]{0,2}$',  # UK-style
        r'^\d{1,3}[A-Z]{1,3}\d{1,4}$',          # Generic
        r'^[A-Z]{3}\s?\d{3,4}$',                  # Simple alpha-numeric
    ]

    @classmethod
    def clean(cls, text: str) -> str:
        """Normalize plate text."""
        # Remove non-alphanumeric except spaces
        cleaned = re.sub(r'[^A-Z0-9 ]', '', text.upper())
        # Collapse multiple spaces
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned

    @classmethod
    def is_valid(cls, text: str) -> bool:
        """Validate plate format."""
        if len(text) < 4 or len(text) > 12:
            return False
        for pattern in cls.PATTERNS:
            if re.match(pattern, text.replace(' ', '')):
                return True
        return len(text) >= 5  # Fallback: accept if at least 5 chars


class ANPRProcessor:
    """
    Complete ANPR pipeline:
    1. Detect license plate in vehicle crop
    2. Enhance image
    3. Run OCR
    4. Validate result
    """

    def __init__(
        self,
        plate_detector=None,
        ocr_languages: List[str] = None,
        min_confidence: float = 0.4,
        cache_size: int = 100,
    ):
        from ..detectors.vehicle_detector import LicensePlateDetector
        self.plate_detector = plate_detector or LicensePlateDetector()
        self.enhancer = ImageEnhancer()
        self.ocr = OCREngine(languages=ocr_languages or ["en"])
        self.validator = PlateTextValidator()
        self.min_confidence = min_confidence
        self._cache: Dict[int, dict] = {}  # track_id -> {plate, confidence, timestamp}

    def process(
        self,
        vehicle_crop: np.ndarray,
        track_id: int = -1,
    ) -> Dict:
        """
        Full ANPR processing pipeline.
        Returns dict with plate_number, confidence, plate_bbox.
        """
        # Check cache (avoid re-processing same vehicle)
        if track_id in self._cache:
            cached = self._cache[track_id]
            if time.time() - cached["timestamp"] < 30.0:  # 30s cache
                return cached

        result = {
            "plate_number": "UNKNOWN",
            "confidence": 0.0,
            "plate_bbox": None,
            "plate_image": None,
        }

        if vehicle_crop is None or vehicle_crop.size == 0:
            return result

        # Step 1: Detect plate location
        plate_bboxes = self.plate_detector.detect_plates(vehicle_crop)

        # Step 2: Process each detected plate
        best_result = result.copy()
        for bbox in plate_bboxes:
            x1, y1, x2, y2 = bbox
            plate_crop = vehicle_crop[y1:y2, x1:x2]
            if plate_crop.size == 0:
                continue

            # Step 3: Enhance plate image
            enhanced = self.enhancer.enhance_plate(plate_crop)
            deskewed = self.enhancer.deskew(enhanced)

            # Step 4: OCR
            text, conf = self.ocr.read(deskewed)
            cleaned = self.validator.clean(text)

            if conf > best_result["confidence"] and self.validator.is_valid(cleaned):
                best_result = {
                    "plate_number": cleaned,
                    "confidence": conf,
                    "plate_bbox": bbox,
                    "plate_image": plate_crop,
                }

        # If plate detection failed, try OCR on full vehicle crop
        if best_result["confidence"] < self.min_confidence:
            enhanced = self.enhancer.enhance_plate(vehicle_crop)
            text, conf = self.ocr.read(enhanced)
            cleaned = self.validator.clean(text)
            if conf > self.min_confidence:
                best_result = {
                    "plate_number": cleaned,
                    "confidence": conf,
                    "plate_bbox": None,
                    "plate_image": vehicle_crop,
                }

        # Cache result
        if track_id >= 0 and best_result["confidence"] >= self.min_confidence:
            self._cache[track_id] = {**best_result, "timestamp": time.time()}

        return best_result

    def get_cached_plate(self, track_id: int) -> Optional[str]:
        """Get cached plate for a track."""
        if track_id in self._cache:
            return self._cache[track_id].get("plate_number")
        return None
