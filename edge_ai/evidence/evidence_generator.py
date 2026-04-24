"""
V-Watch Edge AI - Evidence Generator
Tamper-proof evidence with SHA-256 cryptographic hashing
"""

import cv2
import json
import hashlib
import uuid
import time
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
import numpy as np

logger = logging.getLogger(__name__)


class EvidenceMetadata:
    """Immutable evidence metadata record."""

    def __init__(
        self,
        vehicle_id: str,
        plate_number: str,
        violation_type: str,
        location: str,
        speed: float = 0.0,
        confidence: float = 0.0,
        camera_id: str = "CAM_001",
        additional_data: Dict = None,
    ):
        self.evidence_id = str(uuid.uuid4())
        self.vehicle_id = vehicle_id
        self.plate_number = plate_number
        self.violation_type = violation_type
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.location = location
        self.speed = speed
        self.confidence = confidence
        self.camera_id = camera_id
        self.additional_data = additional_data or {}

        # File paths (set after saving)
        self.frame_image_path: Optional[str] = None
        self.plate_image_path: Optional[str] = None
        self.video_clip_path: Optional[str] = None

        # Cryptographic hashes (set after generating)
        self.frame_hash: Optional[str] = None
        self.plate_hash: Optional[str] = None
        self.video_hash: Optional[str] = None
        self.metadata_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "vehicle_id": self.vehicle_id,
            "plate_number": self.plate_number,
            "violation_type": self.violation_type,
            "timestamp": self.timestamp,
            "location": self.location,
            "speed": self.speed,
            "confidence": self.confidence,
            "camera_id": self.camera_id,
            "additional_data": self.additional_data,
            "files": {
                "frame_image": self.frame_image_path,
                "plate_image": self.plate_image_path,
                "video_clip": self.video_clip_path,
            },
            "hashes": {
                "frame_sha256": self.frame_hash,
                "plate_sha256": self.plate_hash,
                "video_sha256": self.video_hash,
                "metadata_sha256": self.metadata_hash,
            },
        }


class CryptoHasher:
    """SHA-256 cryptographic hashing for evidence integrity."""

    @staticmethod
    def hash_file(file_path: str) -> str:
        """Compute SHA-256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        """Compute SHA-256 hash of raw bytes."""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def hash_json(data: dict) -> str:
        """Compute SHA-256 hash of JSON-serializable dict."""
        json_str = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest()

    @staticmethod
    def verify_file(file_path: str, expected_hash: str) -> bool:
        """Verify file integrity against stored hash."""
        if not os.path.exists(file_path):
            return False
        actual = CryptoHasher.hash_file(file_path)
        return actual == expected_hash

    @staticmethod
    def verify_json(data: dict, expected_hash: str) -> bool:
        """Verify JSON data integrity."""
        actual = CryptoHasher.hash_json(data)
        return actual == expected_hash


class VideoClipRecorder:
    """Records short video clips as violation evidence."""

    def __init__(self, output_dir: str, clip_duration_seconds: int = 10, fps: int = 15):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.clip_duration = clip_duration_seconds
        self.fps = fps
        self._buffer: List[np.ndarray] = []
        self._max_buffer = clip_duration_seconds * fps

    def add_frame(self, frame: np.ndarray):
        """Add frame to rolling buffer."""
        self._buffer.append(frame.copy())
        if len(self._buffer) > self._max_buffer:
            self._buffer.pop(0)

    def save_clip(self, filename: str) -> Optional[str]:
        """Save current buffer as video clip."""
        if not self._buffer:
            return None

        output_path = self.output_dir / filename
        h, w = self._buffer[0].shape[:2]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(output_path), fourcc, self.fps, (w, h))

        for frame in self._buffer:
            writer.write(frame)
        writer.release()
        return str(output_path)


class EvidenceGenerator:
    """
    Main evidence generation engine.
    Captures frames, saves files, generates metadata, and signs with SHA-256.
    """

    def __init__(
        self,
        output_dir: str = "evidence_store",
        camera_id: str = "CAM_001",
        location: str = "Unknown",
        save_video_clips: bool = False,
    ):
        self.output_dir = Path(output_dir)
        self.camera_id = camera_id
        self.location = location
        self.save_video_clips = save_video_clips
        self.hasher = CryptoHasher()

        # Create subdirectories
        for subdir in ["frames", "plates", "clips", "metadata"]:
            (self.output_dir / subdir).mkdir(parents=True, exist_ok=True)

        self._video_recorder: Optional[VideoClipRecorder] = None
        if save_video_clips:
            self._video_recorder = VideoClipRecorder(
                str(self.output_dir / "clips")
            )

    def feed_frame(self, frame: np.ndarray):
        """Feed frame to video buffer (call every frame)."""
        if self._video_recorder:
            self._video_recorder.add_frame(frame)

    def generate(
        self,
        frame: np.ndarray,
        track,
        violation_type: str,
        plate_info: Dict,
        speed: float = 0.0,
        additional_data: Dict = None,
    ) -> EvidenceMetadata:
        """
        Generate complete evidence package for a violation.
        Returns EvidenceMetadata with all hashes set.
        """
        vehicle_id = f"VH_{track.track_id:06d}"
        plate_number = plate_info.get("plate_number", "UNKNOWN")
        confidence = plate_info.get("confidence", 0.0)

        metadata = EvidenceMetadata(
            vehicle_id=vehicle_id,
            plate_number=plate_number,
            violation_type=violation_type,
            location=self.location,
            speed=speed,
            confidence=confidence,
            camera_id=self.camera_id,
            additional_data=additional_data or {},
        )

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_name = f"{ts}_{violation_type}_{track.track_id}"

        # 1. Save violation frame with annotations
        annotated_frame = self._annotate_frame(frame.copy(), track, metadata)
        frame_filename = f"{base_name}_frame.jpg"
        frame_path = self.output_dir / "frames" / frame_filename
        cv2.imwrite(str(frame_path), annotated_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        metadata.frame_image_path = str(frame_path)
        metadata.frame_hash = self.hasher.hash_file(str(frame_path))

        # 2. Save plate image
        plate_img = plate_info.get("plate_image")
        if plate_img is not None and plate_img.size > 0:
            plate_filename = f"{base_name}_plate.jpg"
            plate_path = self.output_dir / "plates" / plate_filename
            cv2.imwrite(str(plate_path), plate_img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            metadata.plate_image_path = str(plate_path)
            metadata.plate_hash = self.hasher.hash_file(str(plate_path))

        # 3. Save video clip
        if self._video_recorder:
            clip_filename = f"{base_name}_clip.mp4"
            clip_path = self._video_recorder.save_clip(clip_filename)
            if clip_path:
                metadata.video_clip_path = clip_path
                metadata.video_hash = self.hasher.hash_file(clip_path)

        # 4. Generate metadata hash (signs all content hashes together)
        hash_chain = {
            "evidence_id": metadata.evidence_id,
            "frame_hash": metadata.frame_hash,
            "plate_hash": metadata.plate_hash,
            "video_hash": metadata.video_hash,
            "timestamp": metadata.timestamp,
            "plate_number": metadata.plate_number,
            "violation_type": metadata.violation_type,
        }
        metadata.metadata_hash = self.hasher.hash_json(hash_chain)

        # 5. Save metadata JSON
        meta_dict = metadata.to_dict()
        meta_filename = f"{base_name}_meta.json"
        meta_path = self.output_dir / "metadata" / meta_filename
        with open(str(meta_path), "w", encoding="utf-8") as f:
            json.dump(meta_dict, f, indent=2, ensure_ascii=False)

        logger.info(
            f"[EvidenceGenerator] Generated evidence {metadata.evidence_id} "
            f"for {violation_type} by vehicle {vehicle_id}"
        )
        return metadata

    def _annotate_frame(self, frame: np.ndarray, track, metadata: EvidenceMetadata) -> np.ndarray:
        """Add forensic annotations to evidence frame."""
        # Draw bounding box
        x1, y1, x2, y2 = track.bbox
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 3)

        # Overlay text
        overlay_lines = [
            f"VIOLATION: {metadata.violation_type}",
            f"PLATE: {metadata.plate_number}",
            f"VEHICLE: {metadata.vehicle_id}",
            f"SPEED: {metadata.speed:.1f} km/h",
            f"TIME: {metadata.timestamp[:19]}",
            f"CAM: {metadata.camera_id}",
            f"LOCATION: {metadata.location}",
        ]
        y_pos = 30
        for line in overlay_lines:
            cv2.putText(frame, line, (10, y_pos),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 4)
            cv2.putText(frame, line, (10, y_pos),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            y_pos += 28

        # Watermark
        h, w = frame.shape[:2]
        cv2.putText(frame, "V-WATCH EVIDENCE", (w - 300, h - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        # Evidence ID (small, bottom)
        cv2.putText(frame, f"ID:{metadata.evidence_id[:8]}", (10, h - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        return frame

    def verify_evidence(self, meta_path: str) -> Dict[str, bool]:
        """Verify integrity of all evidence files."""
        with open(meta_path, "r") as f:
            meta = json.load(f)

        results = {}
        hashes = meta.get("hashes", {})
        files = meta.get("files", {})

        if files.get("frame_image") and hashes.get("frame_sha256"):
            results["frame"] = self.hasher.verify_file(
                files["frame_image"], hashes["frame_sha256"]
            )
        if files.get("plate_image") and hashes.get("plate_sha256"):
            results["plate"] = self.hasher.verify_file(
                files["plate_image"], hashes["plate_sha256"]
            )
        if files.get("video_clip") and hashes.get("video_sha256"):
            results["video"] = self.hasher.verify_file(
                files["video_clip"], hashes["video_sha256"]
            )

        # Verify metadata hash chain
        if hashes.get("metadata_sha256"):
            hash_chain = {
                "evidence_id": meta["evidence_id"],
                "frame_hash": hashes.get("frame_sha256"),
                "plate_hash": hashes.get("plate_sha256"),
                "video_hash": hashes.get("video_sha256"),
                "timestamp": meta["timestamp"],
                "plate_number": meta["plate_number"],
                "violation_type": meta["violation_type"],
            }
            results["metadata_integrity"] = self.hasher.verify_json(
                hash_chain, hashes["metadata_sha256"]
            )

        results["all_valid"] = all(results.values())
        return results
