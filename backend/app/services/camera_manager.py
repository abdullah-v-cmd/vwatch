"""
V-Watch Backend - Persistent Camera Manager Service
====================================================
This module runs camera processing ENTIRELY in the backend, independent of
any frontend connection. Cameras keep running even when users navigate away.

Architecture:
  - Each camera runs in its own background thread (ThreadPoolExecutor)
  - Frames are captured via OpenCV (webcam / RTSP / HTTP MJPEG)
  - YOLO detection runs on each frame at configurable intervals
  - Latest annotated JPEG frame stored in memory per camera (ring buffer)
  - MJPEG stream served over HTTP multipart/x-mixed-replace
  - WebSocket broadcast for violation events (reuses live_monitoring manager)
  - Camera lifecycle (start / stop / restart) fully controlled via REST API
"""

import cv2
import time
import uuid
import base64
import logging
import threading
import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# ── Camera States ─────────────────────────────────────────────────────────────

class CameraState:
    IDLE      = "idle"
    STARTING  = "starting"
    RUNNING   = "running"
    ERROR     = "error"
    STOPPING  = "stopping"
    STOPPED   = "stopped"


# ── Per-Camera Data ────────────────────────────────────────────────────────────

@dataclass
class CameraInfo:
    camera_id: str
    name: str
    source: str                 # 0 / rtsp://... / http://...
    source_type: str            # 'webcam' | 'rtsp' | 'http'
    location: str = ""
    speed_limit: float = 60.0
    enabled: bool = True

    # Runtime state
    state: str = CameraState.IDLE
    fps: float = 0.0
    frame_count: int = 0
    error_message: str = ""
    started_at: Optional[datetime] = None
    last_frame_at: Optional[datetime] = None
    violation_count: int = 0

    # Latest JPEG frame bytes (thread-safe via lock)
    _frame_lock: threading.Lock = field(default_factory=threading.Lock)
    _latest_frame: Optional[bytes] = None
    _stop_event: threading.Event = field(default_factory=threading.Event)

    # Subscribers waiting for new frames (for MJPEG streaming)
    _frame_subscribers: List[asyncio.Queue] = field(default_factory=list)
    _subscribers_lock: threading.Lock = field(default_factory=threading.Lock)

    def set_frame(self, jpeg_bytes: bytes):
        """Store latest JPEG frame and notify all subscribers."""
        with self._frame_lock:
            self._latest_frame = jpeg_bytes
        self.last_frame_at = datetime.now(timezone.utc)
        # Notify async subscribers (non-blocking put_nowait)
        with self._subscribers_lock:
            for q in list(self._frame_subscribers):
                try:
                    # Drain old frames to keep only the latest
                    while not q.empty():
                        try:
                            q.get_nowait()
                        except Exception:
                            pass
                    q.put_nowait(jpeg_bytes)
                except Exception:
                    pass

    def get_frame(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._latest_frame

    def add_subscriber(self, q: asyncio.Queue):
        with self._subscribers_lock:
            self._frame_subscribers.append(q)

    def remove_subscriber(self, q: asyncio.Queue):
        with self._subscribers_lock:
            if q in self._frame_subscribers:
                self._frame_subscribers.remove(q)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "name": self.name,
            "source": self.source,
            "source_type": self.source_type,
            "location": self.location,
            "speed_limit": self.speed_limit,
            "enabled": self.enabled,
            "state": self.state,
            "fps": round(self.fps, 1),
            "frame_count": self.frame_count,
            "error_message": self.error_message,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_frame_at": self.last_frame_at.isoformat() if self.last_frame_at else None,
            "violation_count": self.violation_count,
            "has_stream": self._latest_frame is not None,
        }


# ── YOLO Detection Helper ─────────────────────────────────────────────────────

_yolo_model = None
_yolo_lock = threading.Lock()

VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck", 1: "bicycle"}

CLASS_COLORS = {
    "car":        (0, 255, 0),
    "motorcycle": (0, 140, 255),
    "bus":        (255, 140, 0),
    "truck":      (0, 0, 255),
    "bicycle":    (255, 255, 0),
    "vehicle":    (0, 255, 128),
}

def _get_yolo():
    """Return the singleton YOLO model (loaded once)."""
    global _yolo_model
    if _yolo_model is not None:
        return _yolo_model
    with _yolo_lock:
        if _yolo_model is not None:
            return _yolo_model
        try:
            from ultralytics import YOLO
            import os
            model_path = os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt")
            device = os.environ.get("YOLO_DEVICE", "cpu")
            _yolo_model = YOLO(model_path)
            logger.info(f"[CameraManager] YOLO model loaded: {model_path} on {device}")
        except ImportError:
            logger.warning("[CameraManager] ultralytics not installed – detection in mock mode")
            _yolo_model = None
        except Exception as exc:
            logger.error(f"[CameraManager] YOLO load failed: {exc}")
            _yolo_model = None
    return _yolo_model


def _run_yolo_on_frame(frame, confidence: float = 0.45):
    """
    Run YOLO on a single frame.
    Returns list of dicts: {class, confidence, bbox:[x1,y1,x2,y2], color}
    """
    model = _get_yolo()
    detections = []

    if model is not None:
        try:
            results = model(
                frame,
                conf=confidence,
                verbose=False,
                classes=list(VEHICLE_CLASSES.keys()),
            )
            for r in results:
                if r.boxes is None:
                    continue
                for box in r.boxes:
                    cls_id = int(box.cls[0])
                    conf_val = float(box.conf[0])
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cls_name = VEHICLE_CLASSES.get(cls_id, "vehicle")
                    detections.append({
                        "class": cls_name,
                        "confidence": round(conf_val, 3),
                        "bbox": [x1, y1, x2, y2],
                        "color": CLASS_COLORS.get(cls_name, (0, 255, 0)),
                        "mock": False,
                    })
        except Exception as exc:
            logger.debug(f"[CameraManager] YOLO inference error: {exc}")
    else:
        # Mock mode: generate plausible bounding boxes
        h, w = frame.shape[:2]
        import random
        for _ in range(random.randint(0, 2)):
            cls_name = random.choice(["car", "motorcycle", "bus", "truck"])
            bw = int(w * (0.12 + random.random() * 0.22))
            bh = int(h * (0.10 + random.random() * 0.18))
            bx = random.randint(0, max(0, w - bw))
            by = random.randint(0, max(0, h - bh))
            detections.append({
                "class": cls_name,
                "confidence": round(0.70 + random.random() * 0.28, 3),
                "bbox": [bx, by, bx + bw, by + bh],
                "color": CLASS_COLORS.get(cls_name, (0, 255, 0)),
                "mock": True,
            })

    return detections


def _draw_detections(frame, detections: list):
    """Draw bounding boxes + labels directly on frame (in-place)."""
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        color = det.get("color", (0, 255, 0))
        label = f"{det['class']} {det['confidence']:.0%}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(frame, (x1, max(y1 - th - 8, 0)), (x1 + tw + 6, y1), color, -1)
        cv2.putText(
            frame, label,
            (x1 + 3, max(y1 - 4, th)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA,
        )
    return frame


def _frame_to_jpeg(frame, quality: int = 75) -> Optional[bytes]:
    """Encode a BGR frame to JPEG bytes."""
    try:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            return buf.tobytes()
    except Exception:
        pass
    return None


# ── Camera Capture Thread ─────────────────────────────────────────────────────

def _camera_thread(cam: CameraInfo, broadcast_func=None, confidence: float = 0.45):
    """
    Runs in a background thread. Opens the video source, reads frames,
    runs detection, stores the annotated JPEG, and calls broadcast_func
    for violations. Stops when cam._stop_event is set.
    """
    cam.state = CameraState.STARTING
    cam.error_message = ""
    cam._stop_event.clear()

    logger.info(f"[Camera:{cam.camera_id}] Starting capture thread (source={cam.source})")

    # Resolve source
    source = cam.source
    if source.lower() in ("0", "webcam", "default"):
        source = 0  # OpenCV device index

    cap = None
    try:
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open source: {cam.source}")

        # Configure capture
        if isinstance(source, int):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        cam.state = CameraState.RUNNING
        cam.started_at = datetime.now(timezone.utc)
        logger.info(f"[Camera:{cam.camera_id}] Running ✅")

        fps_counter = 0
        fps_start = time.monotonic()
        detection_frame_skip = 3  # Run YOLO every N frames
        frame_skip_counter = 0

        # Violation throttle: max 1 violation per 8s per camera
        last_violation_time: Dict[str, float] = {}

        while not cam._stop_event.is_set():
            ret, frame = cap.read()
            if not ret:
                # Try to reconnect for RTSP streams
                if cam.source_type in ("rtsp", "http"):
                    logger.warning(f"[Camera:{cam.camera_id}] Frame read failed, reconnecting...")
                    cap.release()
                    time.sleep(2)
                    cap = cv2.VideoCapture(source)
                    if not cap.isOpened():
                        cam.state = CameraState.ERROR
                        cam.error_message = "Stream reconnection failed"
                        time.sleep(5)
                    continue
                else:
                    break

            cam.frame_count += 1
            fps_counter += 1

            # Update FPS every second
            now = time.monotonic()
            elapsed = now - fps_start
            if elapsed >= 1.0:
                cam.fps = fps_counter / elapsed
                fps_counter = 0
                fps_start = now

            # Run detection every N frames
            detections = []
            frame_skip_counter += 1
            if frame_skip_counter >= detection_frame_skip:
                frame_skip_counter = 0
                detections = _run_yolo_on_frame(frame, confidence)

            # Draw annotations
            if detections:
                frame = _draw_detections(frame, detections)

            # Overlay: camera ID + timestamp + FPS
            _draw_overlay(frame, cam)

            # Encode to JPEG and store
            jpeg = _frame_to_jpeg(frame)
            if jpeg:
                cam.set_frame(jpeg)

            # Check for violations (mock: random trigger, real: passed by detector)
            if detections and broadcast_func:
                _check_and_broadcast_violations(
                    cam, detections, last_violation_time, broadcast_func
                )

            # Throttle to ~30 fps max to avoid hammering CPU
            time.sleep(0.005)

    except Exception as exc:
        cam.state = CameraState.ERROR
        cam.error_message = str(exc)
        logger.error(f"[Camera:{cam.camera_id}] Thread error: {exc}")
    finally:
        if cap is not None:
            cap.release()
        cam.state = CameraState.STOPPED
        cam.fps = 0.0
        logger.info(f"[Camera:{cam.camera_id}] Thread stopped")


def _draw_overlay(frame, cam: CameraInfo):
    """Draw semi-transparent info overlay on frame."""
    h, w = frame.shape[:2]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"CAM: {cam.camera_id}",
        ts,
        f"FPS: {cam.fps:.1f}",
    ]
    y = 24
    for line in lines:
        cv2.putText(frame, line, (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(frame, line, (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        y += 22


def _check_and_broadcast_violations(
    cam: CameraInfo,
    detections: list,
    last_violation_time: dict,
    broadcast_func,
):
    """
    Decide whether a violation occurred and broadcast it.
    Real implementations would integrate speed/redlight/direction logic here.
    For demo: randomly trigger with very low probability.
    """
    import random
    VIOLATION_PROB = 0.02  # 2% per detection batch
    if random.random() > VIOLATION_PROB:
        return

    TYPES = ["SPEEDING", "RED_LIGHT", "WRONG_DIRECTION", "LANE_VIOLATION"]
    PLATES = ["ABC-1234", "XYZ-5678", "MNO-3456", "PQR-7890", "DEF-2345"]

    now = time.monotonic()
    vtype = random.choice(TYPES)

    # Throttle: max once per 8 seconds per violation type per camera
    key = f"{cam.camera_id}:{vtype}"
    if now - last_violation_time.get(key, 0) < 8:
        return
    last_violation_time[key] = now

    plate = random.choice(PLATES)
    confidence = round(0.72 + random.random() * 0.26, 3)
    det = detections[0] if detections else {}

    cam.violation_count += 1

    event = {
        "type": "violation",
        "data": {
            "camera_id": cam.camera_id,
            "camera_name": cam.name,
            "location": cam.location,
            "violation_type": vtype,
            "plate_number": plate,
            "confidence": confidence,
            "vehicle_class": det.get("class", "vehicle"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_type": cam.source_type,
        },
    }
    # broadcast_func is a coroutine; schedule it from thread
    try:
        broadcast_func(event)
    except Exception as exc:
        logger.debug(f"[Camera:{cam.camera_id}] Broadcast error: {exc}")


# ── Camera Manager (Singleton) ────────────────────────────────────────────────

class CameraManager:
    """
    Singleton that manages all backend camera capture threads.
    Completely decoupled from frontend – cameras keep running regardless of
    whether any browser is connected.
    """

    def __init__(self):
        self._cameras: Dict[str, CameraInfo] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="cam-")
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._broadcast_callback = None  # Set by live_monitoring router
        logger.info("[CameraManager] Initialised")

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def set_broadcast_callback(self, callback):
        """
        callback(event_dict) → schedules an async broadcast from any thread.
        Injected by live_monitoring module to avoid circular imports.
        """
        self._broadcast_callback = callback

    def _broadcast(self, event: dict):
        """Thread-safe: schedule async broadcast on the asyncio event loop."""
        if self._broadcast_callback and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._broadcast_callback(event), self._loop
                )
            except Exception as exc:
                logger.debug(f"[CameraManager] Broadcast schedule error: {exc}")

    # ── Camera CRUD ───────────────────────────────────────────────────────────

    def add_camera(
        self,
        camera_id: str,
        name: str,
        source: str,
        source_type: str,
        location: str = "",
        speed_limit: float = 60.0,
        enabled: bool = True,
    ) -> CameraInfo:
        with self._lock:
            if camera_id in self._cameras:
                # Update config but don't restart if running
                cam = self._cameras[camera_id]
                cam.name = name
                cam.location = location
                cam.speed_limit = speed_limit
                cam.enabled = enabled
                logger.info(f"[CameraManager] Camera updated: {camera_id}")
                return cam

            cam = CameraInfo(
                camera_id=camera_id,
                name=name,
                source=source,
                source_type=source_type,
                location=location,
                speed_limit=speed_limit,
                enabled=enabled,
            )
            self._cameras[camera_id] = cam
            logger.info(f"[CameraManager] Camera registered: {camera_id} ({source_type})")
            return cam

    def remove_camera(self, camera_id: str) -> bool:
        with self._lock:
            cam = self._cameras.get(camera_id)
            if not cam:
                return False
            self._stop_camera_locked(camera_id)
            del self._cameras[camera_id]
            logger.info(f"[CameraManager] Camera removed: {camera_id}")
            return True

    def get_camera(self, camera_id: str) -> Optional[CameraInfo]:
        return self._cameras.get(camera_id)

    def list_cameras(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [c.to_dict() for c in self._cameras.values()]

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start_camera(self, camera_id: str) -> bool:
        """Start (or restart) a camera capture thread."""
        with self._lock:
            cam = self._cameras.get(camera_id)
            if not cam:
                logger.warning(f"[CameraManager] start_camera: unknown id={camera_id}")
                return False

            if cam.state in (CameraState.RUNNING, CameraState.STARTING):
                logger.info(f"[CameraManager] Camera {camera_id} already running")
                return True

            # Stop any lingering thread
            self._stop_camera_locked(camera_id)

            cam._stop_event.clear()
            cam.state = CameraState.STARTING

            t = threading.Thread(
                target=_camera_thread,
                args=(cam, self._broadcast),
                daemon=True,
                name=f"cam-{camera_id}",
            )
            self._threads[camera_id] = t
            t.start()
            logger.info(f"[CameraManager] Camera thread started: {camera_id}")
            return True

    def stop_camera(self, camera_id: str) -> bool:
        with self._lock:
            return self._stop_camera_locked(camera_id)

    def _stop_camera_locked(self, camera_id: str) -> bool:
        cam = self._cameras.get(camera_id)
        if not cam:
            return False
        cam._stop_event.set()
        cam.state = CameraState.STOPPING
        t = self._threads.get(camera_id)
        if t and t.is_alive():
            t.join(timeout=5)
        self._threads.pop(camera_id, None)
        cam.state = CameraState.STOPPED
        cam.fps = 0.0
        cam._latest_frame = None
        logger.info(f"[CameraManager] Camera stopped: {camera_id}")
        return True

    def restart_camera(self, camera_id: str) -> bool:
        self.stop_camera(camera_id)
        time.sleep(0.5)
        return self.start_camera(camera_id)

    def start_all(self):
        """Start all enabled cameras (called at app startup)."""
        with self._lock:
            ids = [cid for cid, c in self._cameras.items() if c.enabled]
        for cid in ids:
            self.start_camera(cid)

    def stop_all(self):
        """Stop all cameras (called at app shutdown)."""
        with self._lock:
            ids = list(self._cameras.keys())
        for cid in ids:
            self.stop_camera(cid)

    def status_summary(self) -> Dict[str, Any]:
        with self._lock:
            cams = list(self._cameras.values())
        running = sum(1 for c in cams if c.state == CameraState.RUNNING)
        return {
            "total_cameras": len(cams),
            "running": running,
            "stopped": sum(1 for c in cams if c.state == CameraState.STOPPED),
            "error": sum(1 for c in cams if c.state == CameraState.ERROR),
            "cameras": [c.to_dict() for c in cams],
        }


# Singleton instance – imported by all other modules
camera_manager = CameraManager()
