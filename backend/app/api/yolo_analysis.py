"""
V-Watch Backend - YOLO Model Status & Video Analysis API
Admin endpoint to test YOLO detection on uploaded videos.
"""

import uuid
import hashlib
import logging
import time
import json
import base64
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from fastapi import (
    APIRouter, Depends, HTTPException, UploadFile, File, Form,
    BackgroundTasks
)
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import get_db
from ..core.dependencies import require_police
from ..core.config import settings
from ..models.user import User
from ..models.violation import Violation, ViolationStatus, ViolationType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/yolo", tags=["YOLO Analysis"])

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
YOLO_TEMP_DIR = UPLOAD_DIR / "yolo_temp"
YOLO_TEMP_DIR.mkdir(parents=True, exist_ok=True)

# ── Singleton YOLO model state ────────────────────────────────────────────────

_yolo_model = None
_yolo_status: Dict[str, Any] = {
    "loaded": False,
    "model_name": None,
    "device": "cpu",
    "error": None,
    "last_checked": None,
}


def _load_yolo_once(model_path: str = "yolov8n.pt", device: str = "cpu"):
    """Load YOLO model exactly once (singleton)."""
    global _yolo_model, _yolo_status
    if _yolo_model is not None:
        return _yolo_model

    _yolo_status["last_checked"] = datetime.now(timezone.utc).isoformat()
    try:
        from ultralytics import YOLO
        _yolo_model = YOLO(model_path)
        _yolo_status.update({
            "loaded": True,
            "model_name": model_path,
            "device": device,
            "error": None,
        })
        logger.info(f"[YOLO] Model loaded: {model_path} on {device}")
    except ImportError:
        _yolo_status.update({
            "loaded": False,
            "model_name": None,
            "error": "ultralytics not installed – running in mock mode",
        })
        logger.warning("[YOLO] ultralytics not installed. Mock mode active.")
    except Exception as exc:
        _yolo_status.update({
            "loaded": False,
            "model_name": None,
            "error": str(exc),
        })
        logger.error(f"[YOLO] Load error: {exc}")
    return _yolo_model


# Pre-load at import time so the first request is not slow
try:
    _load_yolo_once()
except Exception:
    pass


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/status")
async def yolo_status(current_user: User = Depends(require_police)):
    """
    Return whether the YOLO model is loaded and ready.
    Frontend uses this to show 'YOLO Running' / 'YOLO Offline' badge.
    """
    return {
        "running": _yolo_status["loaded"],
        "model_name": _yolo_status.get("model_name"),
        "device": _yolo_status.get("device", "cpu"),
        "error": _yolo_status.get("error"),
        "last_checked": _yolo_status.get("last_checked"),
        "mock_mode": not _yolo_status["loaded"],
    }


@router.post("/analyze-video")
async def analyze_video(
    file: UploadFile = File(...),
    confidence: float = Form(0.5),
    save_violations: bool = Form(False),
    location: str = Form("Admin Upload Test"),
    camera_id: str = Form("ADMIN_TEST"),
    current_user: User = Depends(require_police),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a video file, run YOLO detection on every Nth frame,
    return detected objects + optionally save violations to DB.

    Supports: MP4, AVI, MOV, MKV (max 200 MB).
    """
    # Validate file type
    allowed_types = {"video/mp4", "video/avi", "video/x-msvideo",
                     "video/quicktime", "video/x-matroska", "video/webm"}
    content_type = (file.content_type or "").lower()
    fname_lower = (file.filename or "").lower()
    if content_type not in allowed_types and not any(
        fname_lower.endswith(ext) for ext in (".mp4", ".avi", ".mov", ".mkv", ".webm")
    ):
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Use MP4, AVI, MOV, MKV, or WebM."
        )

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file uploaded")
    if len(content) > 200 * 1024 * 1024:  # 200 MB
        raise HTTPException(status_code=413, detail="File too large (max 200 MB)")

    # Save temp file
    temp_id = str(uuid.uuid4())
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    temp_path = YOLO_TEMP_DIR / f"{temp_id}{suffix}"
    with open(temp_path, "wb") as f:
        f.write(content)

    try:
        results = await _run_detection(
            video_path=str(temp_path),
            confidence=confidence,
            save_violations=save_violations,
            location=location,
            camera_id=camera_id,
            db=db if save_violations else None,
        )
        return results
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


async def _run_detection(
    video_path: str,
    confidence: float,
    save_violations: bool,
    location: str,
    camera_id: str,
    db: Optional[AsyncSession],
) -> Dict[str, Any]:
    """Run YOLO detection on video frames and return structured results."""
    import cv2

    # VEHICLE_CLASSES from COCO
    VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 1: "bicycle"}

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise HTTPException(status_code=400, detail="Could not open video file")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    duration_s = total_frames / fps
    sample_every = max(1, int(fps * 2))  # Sample every 2 seconds

    model = _load_yolo_once()

    detections_per_frame: List[Dict] = []
    thumbnail_b64: Optional[str] = None
    frame_idx = 0
    vehicle_count = 0
    violations_created = []

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % sample_every == 0:
            frame_detections = []

            if model is not None:
                try:
                    results = model(
                        frame,
                        conf=confidence,
                        verbose=False,
                        classes=list(VEHICLE_CLASSES.keys()),
                    )
                    for result in results:
                        if result.boxes is None:
                            continue
                        for box in result.boxes:
                            cls_id = int(box.cls[0])
                            conf_val = float(box.conf[0])
                            x1, y1, x2, y2 = map(int, box.xyxy[0])
                            class_name = VEHICLE_CLASSES.get(cls_id, "vehicle")
                            frame_detections.append({
                                "class": class_name,
                                "confidence": round(conf_val, 3),
                                "bbox": [x1, y1, x2, y2],
                                "frame": frame_idx,
                                "time_s": round(frame_idx / fps, 2),
                            })
                            vehicle_count += 1

                            # Draw bbox on frame
                            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                            label = f"{class_name} {conf_val:.2f}"
                            cv2.putText(frame, label, (x1, max(y1 - 6, 14)),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                except Exception as e:
                    logger.error(f"[YOLO] Inference error at frame {frame_idx}: {e}")
            else:
                # Mock mode – return placeholder detection
                h, w = frame.shape[:2]
                frame_detections.append({
                    "class": "car",
                    "confidence": 0.92,
                    "bbox": [int(w * 0.2), int(h * 0.3), int(w * 0.5), int(h * 0.7)],
                    "frame": frame_idx,
                    "time_s": round(frame_idx / fps, 2),
                    "mock": True,
                })
                vehicle_count += 1

            if frame_detections:
                detections_per_frame.extend(frame_detections)

            # Save first annotated frame as thumbnail
            if thumbnail_b64 is None and frame_idx == 0:
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                thumbnail_b64 = base64.b64encode(buf.tobytes()).decode()

            # Optionally save violation records (speeding mock)
            if save_violations and db and frame_detections:
                for det in frame_detections[:1]:  # one per sample frame max
                    if det.get("mock") and vehicle_count % 3 == 0:
                        continue  # Skip most mocks to avoid flood
                    ev_id = str(uuid.uuid4())
                    from sqlalchemy import select
                    dup = await db.execute(
                        select(Violation).where(Violation.evidence_id == ev_id)
                    )
                    if dup.scalar_one_or_none():
                        continue
                    v = Violation(
                        evidence_id=ev_id,
                        vehicle_id=f"YOLO_{ev_id[:8]}",
                        plate_number="UNKNOWN",
                        vehicle_type=det["class"],
                        violation_type=ViolationType.SPEEDING,
                        status=ViolationStatus.PENDING,
                        location=location,
                        camera_id=camera_id,
                        violation_time=datetime.now(timezone.utc),
                        confidence=det["confidence"],
                        fine_amount=200.0,
                    )
                    db.add(v)
                    violations_created.append(ev_id)
                if violations_created:
                    await db.commit()

        frame_idx += 1

    cap.release()

    return {
        "success": True,
        "yolo_running": _yolo_status["loaded"],
        "mock_mode": not _yolo_status["loaded"],
        "video_info": {
            "total_frames": total_frames,
            "fps": round(fps, 2),
            "duration_s": round(duration_s, 2),
            "frames_sampled": len([d for d in detections_per_frame]),
        },
        "summary": {
            "total_detections": len(detections_per_frame),
            "unique_vehicles_approx": vehicle_count,
            "violations_saved": len(violations_created) if save_violations else 0,
        },
        "detections": detections_per_frame[:200],  # cap at 200
        "thumbnail_b64": thumbnail_b64,
    }
