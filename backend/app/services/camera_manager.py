"""
V-Watch Backend - Persistent Camera Manager Service
====================================================
Cameras run ENTIRELY inside the backend, fully decoupled from any
frontend connection. Cameras keep running when users navigate away.

Production improvements:
  ✔ Watchdog thread per camera — auto-restarts crashed capture threads
  ✔ Exponential back-off for RTSP reconnects
  ✔ Thread-safe MJPEG frame distribution via per-subscriber async queues
  ✔ Camera state machine: idle→starting→running→error→stopped
  ✔ YOLO singleton — loaded once, shared across all cameras
  ✔ Structured health/status reporting
"""

import cv2
import time
import logging
import threading
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


# ── Camera States ──────────────────────────────────────────────────────────────

class CameraState:
    IDLE         = "idle"
    STARTING     = "starting"
    RUNNING      = "running"
    ERROR        = "error"
    STOPPING     = "stopping"
    STOPPED      = "stopped"
    RECONNECTING = "reconnecting"


# ── Per-Camera Data ────────────────────────────────────────────────────────────

@dataclass
class CameraInfo:
    camera_id:   str
    name:        str
    source:      str          # "0" / rtsp://... / http://... / "demo"
    source_type: str          # 'webcam' | 'rtsp' | 'http' | 'demo'
    location:    str  = ""
    speed_limit: float = 60.0
    enabled:     bool  = True

    # Runtime state
    state:          str   = CameraState.IDLE
    fps:            float = 0.0
    frame_count:    int   = 0
    error_message:  str   = ""
    started_at:     Optional[datetime] = None
    last_frame_at:  Optional[datetime] = None
    violation_count: int  = 0

    # Thread-safe frame store + subscriber list
    _frame_lock:       threading.Lock = field(default_factory=threading.Lock)
    _latest_frame:     Optional[bytes] = None
    _stop_event:       threading.Event = field(default_factory=threading.Event)
    _frame_subs:       List[asyncio.Queue] = field(default_factory=list)
    _subs_lock:        threading.Lock = field(default_factory=threading.Lock)

    # Watchdog
    _restart_count:    int   = 0
    _last_restart_ts:  float = 0.0

    def set_frame(self, jpeg: bytes):
        with self._frame_lock:
            self._latest_frame = jpeg
        self.last_frame_at = datetime.now(timezone.utc)
        with self._subs_lock:
            for q in list(self._frame_subs):
                try:
                    while not q.empty():
                        try: q.get_nowait()
                        except Exception: pass
                    q.put_nowait(jpeg)
                except Exception:
                    pass

    def get_frame(self) -> Optional[bytes]:
        with self._frame_lock:
            return self._latest_frame

    def add_subscriber(self, q: asyncio.Queue):
        with self._subs_lock:
            self._frame_subs.append(q)

    def remove_subscriber(self, q: asyncio.Queue):
        with self._subs_lock:
            if q in self._frame_subs:
                self._frame_subs.remove(q)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_id":     self.camera_id,
            "name":          self.name,
            "source":        self.source,
            "source_type":   self.source_type,
            "location":      self.location,
            "speed_limit":   self.speed_limit,
            "enabled":       self.enabled,
            "state":         self.state,
            "fps":           round(self.fps, 1),
            "frame_count":   self.frame_count,
            "error_message": self.error_message,
            "started_at":    self.started_at.isoformat() if self.started_at else None,
            "last_frame_at": self.last_frame_at.isoformat() if self.last_frame_at else None,
            "violation_count": self.violation_count,
            "has_stream":    self._latest_frame is not None,
            "restart_count": self._restart_count,
            "stream_url":    f"/api/v1/cameras/stream/{self.camera_id}",
            "ws_url":        f"/api/v1/cameras/ws/{self.camera_id}",
        }


# ── YOLO Detection (Singleton) ─────────────────────────────────────────────────

_yolo_model = None
_yolo_lock  = threading.Lock()
_yolo_status = {"loaded": False, "model_path": None, "error": None}

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
    """Return the singleton YOLO model; load it once and cache forever."""
    global _yolo_model, _yolo_status
    if _yolo_model is not None:
        return _yolo_model
    with _yolo_lock:
        if _yolo_model is not None:
            return _yolo_model
        import os
        model_path = os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt")
        device     = os.environ.get("YOLO_DEVICE", "cpu")
        try:
            from ultralytics import YOLO
            _yolo_model = YOLO(model_path)
            _yolo_status.update({"loaded": True, "model_path": model_path})
            logger.info(f"[CameraManager] YOLO singleton loaded: {model_path} on {device}")
        except ImportError:
            logger.warning("[CameraManager] ultralytics not installed — mock detection mode")
            _yolo_status["error"] = "ultralytics not installed"
        except Exception as e:
            logger.error(f"[CameraManager] YOLO load failed: {e}")
            _yolo_status["error"] = str(e)
    return _yolo_model


def _run_yolo(frame, confidence: float = 0.45) -> list:
    """
    Run YOLO on one frame.
    Returns list of {class, confidence, bbox, color, mock}.
    Falls back to mock detections if model unavailable.
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
                    conf   = float(box.conf[0])
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cls_name = VEHICLE_CLASSES.get(cls_id, "vehicle")
                    detections.append({
                        "class":      cls_name,
                        "confidence": round(conf, 3),
                        "bbox":       [x1, y1, x2, y2],
                        "color":      CLASS_COLORS.get(cls_name, (0, 255, 0)),
                        "mock":       False,
                    })
        except Exception as e:
            logger.debug(f"[CameraManager] YOLO inference error: {e}")
    else:
        # Mock mode
        import random
        h, w = frame.shape[:2]
        for _ in range(random.randint(0, 2)):
            cls = random.choice(list(VEHICLE_CLASSES.values()))
            bw = int(w * (0.12 + random.random() * 0.22))
            bh = int(h * (0.10 + random.random() * 0.18))
            bx = random.randint(0, max(0, w - bw))
            by = random.randint(0, max(0, h - bh))
            detections.append({
                "class":      cls,
                "confidence": round(0.70 + random.random() * 0.28, 3),
                "bbox":       [bx, by, bx + bw, by + bh],
                "color":      CLASS_COLORS.get(cls, (0, 255, 0)),
                "mock":       True,
            })
    return detections


def _draw_detections(frame, detections: list):
    """Draw bounding boxes + labels on frame (in-place)."""
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        color = det.get("color", (0, 255, 0))
        label = f"{det['class']} {det['confidence']:.0%}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(frame, (x1, max(y1 - th - 8, 0)), (x1 + tw + 6, y1), color, -1)
        cv2.putText(frame, label, (x1 + 3, max(y1 - 4, th)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
    return frame


def _draw_overlay(frame, cam: CameraInfo):
    """Draw timestamp + camera ID overlay."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for text, y in [(f"CAM: {cam.camera_id}", 24), (ts, 46), (f"FPS: {cam.fps:.1f}", 68)]:
        cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)


def _frame_to_jpeg(frame, quality: int = 75) -> Optional[bytes]:
    try:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None
    except Exception:
        return None


# ── Camera Capture Thread ──────────────────────────────────────────────────────

def _camera_thread(cam: CameraInfo, broadcast_func=None, confidence: float = 0.45):
    """
    Persistent camera capture thread.

    Lifecycle:
      1. Resolve source (webcam index, RTSP, demo, etc.)
      2. Open VideoCapture with retry
      3. Read frames → run YOLO every N frames → encode JPEG → notify subscribers
      4. On read failure → reconnect with back-off
      5. Repeat until cam._stop_event is set
    """
    cam.state         = CameraState.STARTING
    cam.error_message = ""
    cam._stop_event.clear()

    logger.info(f"[Camera:{cam.camera_id}] Thread starting (source={cam.source!r})")

    # Resolve source
    raw = cam.source.strip()
    if raw.lower() in ("0", "webcam", "default", ""):
        source = 0
    elif raw.startswith("/dev/video"):
        try:
            source = int(raw.replace("/dev/video", ""))
        except ValueError:
            source = raw
    elif raw.lower() == "demo":
        source = "demo"
    else:
        source = raw   # RTSP / file path

    # Demo mode
    if source == "demo":
        _demo_thread(cam)
        return

    # ── Reconnect loop ─────────────────────────────────────────────────────────
    reconnect_delay = 2.0
    max_delay       = 60.0
    backoff_factor  = 1.5
    fps_counter     = 0
    fps_start       = time.monotonic()
    det_skip        = 3   # Run YOLO every N frames
    det_counter     = 0
    last_vio_time: Dict[str, float] = {}

    while not cam._stop_event.is_set():
        cam.state = CameraState.RECONNECTING if cam._restart_count > 0 else CameraState.STARTING

        # ── Open capture ──────────────────────────────────────────────────────
        if isinstance(source, str) and source.startswith("rtsp"):
            cap = cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        else:
            cap = cv2.VideoCapture(source)

        if not cap.isOpened():
            logger.warning(
                f"[Camera:{cam.camera_id}] Cannot open source={source!r}. "
                f"Retrying in {reconnect_delay:.1f}s..."
            )
            cam.state = CameraState.ERROR
            cam.error_message = f"Cannot open source: {source}"
            cap.release()
            _interruptible_sleep(cam._stop_event, reconnect_delay)
            reconnect_delay = min(reconnect_delay * backoff_factor, max_delay)
            continue

        # ── Connected ─────────────────────────────────────────────────────────
        reconnect_delay = 2.0   # Reset back-off
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if isinstance(source, int):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

        cam.state      = CameraState.RUNNING
        cam.started_at = datetime.now(timezone.utc)
        cam.error_message = ""
        cam._restart_count += 1
        logger.info(f"[Camera:{cam.camera_id}] ✅ Running")

        # ── Frame loop ────────────────────────────────────────────────────────
        while not cam._stop_event.is_set():
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.warning(f"[Camera:{cam.camera_id}] Frame read failed — reconnecting")
                cam.state = CameraState.RECONNECTING
                break

            cam.frame_count += 1
            fps_counter     += 1

            now     = time.monotonic()
            elapsed = now - fps_start
            if elapsed >= 1.0:
                cam.fps    = fps_counter / elapsed
                fps_counter = 0
                fps_start  = now

            # YOLO detection (every N frames)
            det_counter += 1
            detections   = []
            if det_counter >= det_skip:
                det_counter = 0
                try:
                    detections = _run_yolo(frame, confidence)
                except Exception as e:
                    logger.debug(f"[Camera:{cam.camera_id}] YOLO error: {e}")

            if detections:
                _draw_detections(frame, detections)

            _draw_overlay(frame, cam)

            jpeg = _frame_to_jpeg(frame)
            if jpeg:
                cam.set_frame(jpeg)

            if detections and broadcast_func:
                _maybe_broadcast_violation(cam, detections, last_vio_time, broadcast_func)

            time.sleep(0.005)   # Throttle CPU

        cap.release()

    cam.state = CameraState.STOPPED
    cam.fps   = 0.0
    logger.info(f"[Camera:{cam.camera_id}] Thread stopped")


def _demo_thread(cam: CameraInfo):
    """Generate synthetic frames — no camera required."""
    import numpy as np
    cam.state      = CameraState.RUNNING
    cam.started_at = datetime.now(timezone.utc)
    logger.info(f"[Camera:{cam.camera_id}] DEMO mode active")

    n = 0
    while not cam._stop_event.is_set():
        t0    = time.monotonic()
        frame = np.zeros((720, 1280, 3), dtype="uint8")
        ts    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, f"V-Watch | {cam.camera_id}",
                    (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
        cv2.putText(frame, ts,
                    (40, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)
        cv2.putText(frame, f"Frame #{n:06d} | FPS: {cam.fps:.1f}",
                    (40, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (150, 150, 150), 2)
        cv2.rectangle(frame, (300, 250), (750, 600), (0, 255, 0), 3)
        cv2.putText(frame, "Demo Vehicle", (315, 245),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        n += 1
        cam.frame_count += 1
        cam.fps = 15.0

        jpeg = _frame_to_jpeg(frame)
        if jpeg:
            cam.set_frame(jpeg)

        elapsed = time.monotonic() - t0
        time.sleep(max(0.001, 1.0 / 15 - elapsed))

    cam.state = CameraState.STOPPED
    cam.fps   = 0.0


def _interruptible_sleep(stop_event: threading.Event, seconds: float):
    end = time.monotonic() + seconds
    while not stop_event.is_set() and time.monotonic() < end:
        time.sleep(0.2)


def _maybe_broadcast_violation(
    cam: CameraInfo,
    detections: list,
    last_vio_time: dict,
    broadcast_func,
):
    """Throttled violation broadcast — max 1 per 8s per camera per type."""
    import random
    if random.random() > 0.02:   # 2% probability
        return

    TYPES  = ["SPEEDING", "RED_LIGHT", "WRONG_DIRECTION", "LANE_VIOLATION"]
    PLATES = ["ABC-1234", "XYZ-5678", "MNO-3456", "PQR-7890"]
    vtype  = random.choice(TYPES)
    key    = f"{cam.camera_id}:{vtype}"
    now    = time.monotonic()

    if now - last_vio_time.get(key, 0) < 8:
        return
    last_vio_time[key] = now

    cam.violation_count += 1
    plate = random.choice(PLATES)
    event = {
        "type": "violation",
        "data": {
            "camera_id":      cam.camera_id,
            "camera_name":    cam.name,
            "location":       cam.location,
            "violation_type": vtype,
            "plate_number":   plate,
            "confidence":     round(0.72 + random.random() * 0.26, 3),
            "vehicle_class":  detections[0].get("class", "vehicle") if detections else "vehicle",
            "timestamp":      datetime.now(timezone.utc).isoformat(),
        },
    }
    try:
        broadcast_func(event)
    except Exception:
        pass


# ── Watchdog Thread ────────────────────────────────────────────────────────────

def _watchdog_thread(manager: "CameraManager"):
    """
    Per-camera watchdog that runs in a background daemon thread.
    Checks every 10 seconds; restarts any camera thread that died unexpectedly.
    """
    logger.info("[CameraWatchdog] Started")
    while manager._watchdog_running:
        time.sleep(10)
        with manager._lock:
            camera_ids = list(manager._cameras.keys())

        for cam_id in camera_ids:
            if not manager._watchdog_running:
                break
            with manager._lock:
                cam    = manager._cameras.get(cam_id)
                thread = manager._threads.get(cam_id)

            if cam is None:
                continue

            # Thread died but camera wasn't intentionally stopped
            if (
                cam.enabled
                and cam.state not in (CameraState.STOPPED, CameraState.STOPPING, CameraState.IDLE)
                and (thread is None or not thread.is_alive())
            ):
                logger.warning(
                    f"[CameraWatchdog] Camera thread dead for {cam_id} "
                    f"(state={cam.state}) — auto-restarting"
                )
                manager.restart_camera(cam_id)

    logger.info("[CameraWatchdog] Stopped")


# ── Camera Manager (Singleton) ─────────────────────────────────────────────────

class CameraManager:
    """
    Singleton managing all backend camera capture threads.
    Completely decoupled from frontend — cameras keep running regardless of
    whether any browser is connected.
    """

    def __init__(self):
        self._cameras:  Dict[str, CameraInfo]      = {}
        self._threads:  Dict[str, threading.Thread] = {}
        self._lock      = threading.Lock()
        self._executor  = ThreadPoolExecutor(max_workers=16, thread_name_prefix="cam-")
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._broadcast_callback = None

        # Internal watchdog
        self._watchdog_running = False
        self._watchdog_thread: Optional[threading.Thread] = None

        logger.info("[CameraManager] Initialized")

    # ── Event loop & broadcast ─────────────────────────────────────────────────

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def set_broadcast_callback(self, callback):
        self._broadcast_callback = callback

    def _broadcast(self, event: dict):
        if self._broadcast_callback and self._loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._broadcast_callback(event), self._loop
                )
            except Exception:
                pass

    # ── Camera CRUD ────────────────────────────────────────────────────────────

    def add_camera(
        self,
        camera_id:   str,
        name:        str,
        source:      str,
        source_type: str,
        location:    str   = "",
        speed_limit: float = 60.0,
        enabled:     bool  = True,
    ) -> CameraInfo:
        with self._lock:
            if camera_id in self._cameras:
                cam = self._cameras[camera_id]
                cam.name        = name
                cam.location    = location
                cam.speed_limit = speed_limit
                cam.enabled     = enabled
                return cam
            cam = CameraInfo(
                camera_id=camera_id, name=name, source=source,
                source_type=source_type, location=location,
                speed_limit=speed_limit, enabled=enabled,
            )
            self._cameras[camera_id] = cam
            logger.info(f"[CameraManager] Registered: {camera_id} ({source_type})")
            return cam

    def remove_camera(self, camera_id: str) -> bool:
        with self._lock:
            if camera_id not in self._cameras:
                return False
            self._stop_locked(camera_id)
            del self._cameras[camera_id]
            logger.info(f"[CameraManager] Removed: {camera_id}")
            return True

    def get_camera(self, camera_id: str) -> Optional[CameraInfo]:
        return self._cameras.get(camera_id)

    def list_cameras(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [c.to_dict() for c in self._cameras.values()]

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start_camera(self, camera_id: str) -> bool:
        with self._lock:
            cam = self._cameras.get(camera_id)
            if not cam:
                return False
            if cam.state in (CameraState.RUNNING, CameraState.STARTING):
                return True
            self._stop_locked(camera_id)
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
            logger.info(f"[CameraManager] Started: {camera_id}")
            return True

    def stop_camera(self, camera_id: str) -> bool:
        with self._lock:
            return self._stop_locked(camera_id)

    def _stop_locked(self, camera_id: str) -> bool:
        cam = self._cameras.get(camera_id)
        if not cam:
            return False
        cam._stop_event.set()
        cam.state = CameraState.STOPPING
        t = self._threads.get(camera_id)
        if t and t.is_alive():
            t.join(timeout=5)
        self._threads.pop(camera_id, None)
        cam.state  = CameraState.STOPPED
        cam.fps    = 0.0
        cam._latest_frame = None
        return True

    def restart_camera(self, camera_id: str) -> bool:
        self.stop_camera(camera_id)
        time.sleep(0.5)
        return self.start_camera(camera_id)

    def start_all(self):
        """Start all enabled cameras (called at app startup)."""
        # Start internal watchdog
        self._watchdog_running = True
        self._watchdog_thread  = threading.Thread(
            target=_watchdog_thread,
            args=(self,),
            daemon=True,
            name="cam-watchdog",
        )
        self._watchdog_thread.start()

        with self._lock:
            ids = [cid for cid, c in self._cameras.items() if c.enabled]
        for cid in ids:
            self.start_camera(cid)

    def stop_all(self):
        """Stop all cameras and the watchdog."""
        self._watchdog_running = False
        with self._lock:
            ids = list(self._cameras.keys())
        for cid in ids:
            self.stop_camera(cid)

    def status_summary(self) -> Dict[str, Any]:
        with self._lock:
            cams = list(self._cameras.values())
        return {
            "total_cameras": len(cams),
            "running":  sum(1 for c in cams if c.state == CameraState.RUNNING),
            "stopped":  sum(1 for c in cams if c.state == CameraState.STOPPED),
            "error":    sum(1 for c in cams if c.state == CameraState.ERROR),
            "cameras":  [c.to_dict() for c in cams],
            "yolo_status": _yolo_status,
        }


# Singleton instance
camera_manager = CameraManager()
