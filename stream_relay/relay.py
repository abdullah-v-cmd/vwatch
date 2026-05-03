"""
V-Watch Stream Relay Service
==============================
Persistent MJPEG/HLS stream relay that keeps camera streams alive
independently of Edge AI container state or frontend connections.

Features:
  - Reads from camera source (webcam / RTSP / file / demo)
  - Serves MJPEG stream on HTTP (embeddable as <img src="...">)
  - Serves latest JPEG snapshot on /snapshot/{cam_id}
  - Survives frontend page navigation (pure HTTP, no WebSocket)
  - Auto-reconnects camera source on failure
  - Multi-camera support via config
  - Health endpoint for watchdog
"""

import cv2
import os
import sys
import json
import time
import signal
import logging
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Optional, List
from logging.handlers import RotatingFileHandler

# ── Logging ────────────────────────────────────────────────────────────────────

LOG_DIR = os.environ.get("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] RELAY: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(
            os.path.join(LOG_DIR, "relay.log"),
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("relay")


# ── Camera Source ──────────────────────────────────────────────────────────────

class CameraSource:
    """
    One persistent camera source.
    Runs a background thread that continuously reads frames.
    Auto-reconnects on failure with exponential back-off.
    """

    PLACEHOLDER_JPEG: Optional[bytes] = None

    def __init__(self, cam_id: str, source, target_fps: int = 15, resize=(1280, 720)):
        self.cam_id     = cam_id
        self.source     = source
        self.target_fps = target_fps
        self.resize     = resize

        self._lock     = threading.Lock()
        self._frame: Optional[bytes] = None   # Latest JPEG bytes
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._state    = "idle"
        self._frames   = 0
        self._fps      = 0.0

        # Subscribers: list of threading.Event + deque pairs
        self._subs: List[threading.Event] = []
        self._sub_lock = threading.Lock()

        self._is_demo  = str(source).lower() == "demo"

    def _resolve_source(self):
        src = self.source
        if isinstance(src, int):
            return src
        s = str(src).strip()
        if s.isdigit():
            return int(s)
        if s.startswith("/dev/video"):
            try:
                return int(s.replace("/dev/video", ""))
            except ValueError:
                pass
        return s

    def start(self):
        if self._running:
            return
        self._running = True
        target = self._demo_loop if self._is_demo else self._capture_loop
        self._thread = threading.Thread(
            target=target,
            daemon=True,
            name=f"relay-{self.cam_id}",
        )
        self._thread.start()
        logger.info(f"[{self.cam_id}] Source thread started (source={self.source})")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info(f"[{self.cam_id}] Source thread stopped")

    def get_frame(self) -> bytes:
        """Return latest JPEG bytes, or placeholder if not available."""
        with self._lock:
            if self._frame:
                return self._frame
        return self._get_placeholder()

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def state(self) -> str:
        return self._state

    @property
    def frame_count(self) -> int:
        return self._frames

    def _store_frame(self, frame):
        """Encode frame to JPEG and store."""
        try:
            ok, buf = cv2.imencode(
                ".jpg", frame,
                [cv2.IMWRITE_JPEG_QUALITY, 75]
            )
            if ok:
                jpeg = buf.tobytes()
                with self._lock:
                    self._frame = jpeg
                self._frames += 1
                self._notify_subs()
        except Exception as e:
            logger.debug(f"[{self.cam_id}] JPEG encode error: {e}")

    def _notify_subs(self):
        with self._sub_lock:
            for ev in self._subs:
                ev.set()

    def subscribe(self) -> threading.Event:
        ev = threading.Event()
        with self._sub_lock:
            self._subs.append(ev)
        return ev

    def unsubscribe(self, ev: threading.Event):
        with self._sub_lock:
            if ev in self._subs:
                self._subs.remove(ev)

    def _capture_loop(self):
        """Capture loop with auto-reconnect."""
        delay    = 2.0
        max_delay = 60.0
        factor   = 1.5
        interval = 1.0 / max(self.target_fps, 1)

        fps_counter = 0
        fps_timer   = time.monotonic()

        while self._running:
            self._state = "connecting"
            logger.info(f"[{self.cam_id}] Connecting to source: {self.source}")

            resolved = self._resolve_source()
            if isinstance(resolved, str) and resolved.startswith("rtsp"):
                cap = cv2.VideoCapture(resolved, cv2.CAP_FFMPEG)
            else:
                cap = cv2.VideoCapture(resolved)

            if not cap.isOpened():
                logger.warning(
                    f"[{self.cam_id}] Cannot open source. "
                    f"Retrying in {delay:.1f}s..."
                )
                cap.release()
                self._state = "reconnecting"
                self._sleep(delay)
                delay = min(delay * factor, max_delay)
                continue

            # Connected
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if isinstance(resolved, int):
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.resize[0])
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resize[1])
                cap.set(cv2.CAP_PROP_FPS,          self.target_fps)

            self._state = "running"
            delay = 2.0   # Reset back-off
            logger.info(f"[{self.cam_id}] ✅ Stream connected")

            while self._running:
                t0  = time.monotonic()
                ret, frame = cap.read()

                if not ret or frame is None:
                    logger.warning(f"[{self.cam_id}] Frame read failed — reconnecting")
                    self._state = "reconnecting"
                    break

                if self.resize:
                    try:
                        frame = cv2.resize(frame, self.resize)
                    except Exception:
                        pass

                self._store_frame(frame)

                fps_counter += 1
                elapsed = time.monotonic() - fps_timer
                if elapsed >= 1.0:
                    self._fps = fps_counter / elapsed
                    fps_counter = 0
                    fps_timer   = time.monotonic()

                # FPS throttle
                proc = time.monotonic() - t0
                sleep = max(0.001, interval - proc)
                time.sleep(sleep)

            cap.release()

        self._state = "stopped"
        logger.info(f"[{self.cam_id}] Capture loop ended")

    def _demo_loop(self):
        """Synthetic frame loop for no-camera demo mode."""
        import numpy as np
        self._state = "running"
        interval    = 1.0 / max(self.target_fps, 1)
        n           = 0

        logger.info(f"[{self.cam_id}] DEMO mode active")

        while self._running:
            t0 = time.monotonic()
            frame = np.zeros((720, 1280, 3), dtype="uint8")
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(frame, f"V-Watch RELAY | {self.cam_id}",
                        (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 255, 255), 3)
            cv2.putText(frame, ts,
                        (40, 140), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (200, 200, 200), 2)
            cv2.putText(frame, f"Frame #{n:06d}",
                        (40, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (150, 150, 150), 2)
            cv2.rectangle(frame, (300, 300), (700, 600), (0, 255, 0), 3)
            cv2.putText(frame, "Simulated Object",
                        (310, 295), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            n += 1
            self._fps = self.target_fps
            self._store_frame(frame)
            elapsed = time.monotonic() - t0
            time.sleep(max(0.001, interval - elapsed))

        self._state = "stopped"

    def _sleep(self, seconds: float):
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(0.2)

    @classmethod
    def _get_placeholder(cls) -> bytes:
        """Generate a static 'Offline' JPEG placeholder."""
        if cls.PLACEHOLDER_JPEG is None:
            try:
                import numpy as np
                img = np.zeros((480, 640, 3), dtype="uint8")
                img[:] = (30, 30, 30)
                cv2.putText(img, "Stream Offline",
                            (160, 230), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (200, 200, 200), 2)
                cv2.putText(img, "Reconnecting...",
                            (180, 270), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (120, 120, 120), 1)
                _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
                cls.PLACEHOLDER_JPEG = buf.tobytes()
            except Exception:
                cls.PLACEHOLDER_JPEG = b""
        return cls.PLACEHOLDER_JPEG


# ── MJPEG HTTP Handler ─────────────────────────────────────────────────────────

BOUNDARY = b"--vwatchframe"

class RelayHTTPHandler(BaseHTTPRequestHandler):
    """
    HTTP handler serving:
      GET /stream/{cam_id}    → MJPEG multipart stream
      GET /snapshot/{cam_id}  → Single JPEG frame
      GET /health             → JSON health status
      GET /cameras            → JSON camera list
    """

    cameras: Dict[str, CameraSource] = {}   # Injected by server setup

    def log_message(self, fmt, *args):
        pass  # Silence per-request access logs

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")

        if path.startswith("/stream/"):
            cam_id = path[len("/stream/"):]
            self._serve_mjpeg(cam_id)

        elif path.startswith("/snapshot/"):
            cam_id = path[len("/snapshot/"):]
            self._serve_snapshot(cam_id)

        elif path == "/health":
            self._serve_health()

        elif path == "/cameras":
            self._serve_cameras()

        else:
            self.send_response(404)
            self.end_headers()

    def _serve_mjpeg(self, cam_id: str):
        cam = self.cameras.get(cam_id)
        if cam is None:
            # Return placeholder stream even for unknown cameras
            cam = None

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "multipart/x-mixed-replace;boundary=vwatchframe"
        )
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        logger.info(
            f"[Relay] MJPEG stream opened: cam={cam_id} "
            f"client={self.client_address[0]}"
        )

        # Subscribe to new frames
        ev = cam.subscribe() if cam else threading.Event()

        try:
            while True:
                # Wait for new frame (max 2 seconds before sending current)
                ev.wait(timeout=2.0)
                ev.clear()

                jpeg = cam.get_frame() if cam else CameraSource._get_placeholder()
                if not jpeg:
                    continue

                header = (
                    BOUNDARY + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
                )
                try:
                    self.wfile.write(header + jpeg + b"\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                except Exception:
                    break
        finally:
            if cam:
                cam.unsubscribe(ev)
            logger.info(f"[Relay] MJPEG stream closed: cam={cam_id}")

    def _serve_snapshot(self, cam_id: str):
        cam = self.cameras.get(cam_id)
        jpeg = cam.get_frame() if cam else CameraSource._get_placeholder()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(jpeg)

    def _serve_health(self):
        data = json.dumps({
            "status": "ok",
            "service": "stream_relay",
            "cameras": {
                cid: {
                    "state": c.state,
                    "fps":   round(c.fps, 1),
                    "frames": c.frame_count,
                }
                for cid, c in self.cameras.items()
            },
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def _serve_cameras(self):
        data = json.dumps({
            "cameras": [
                {
                    "id":    cid,
                    "state": c.state,
                    "fps":   round(c.fps, 1),
                    "stream_url": f"/stream/{cid}",
                    "snapshot_url": f"/snapshot/{cid}",
                }
                for cid, c in self.cameras.items()
            ]
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


# ── Relay Server ───────────────────────────────────────────────────────────────

class StreamRelayServer:
    """Main relay server managing multiple camera sources."""

    def __init__(self):
        self.cameras: Dict[str, CameraSource] = {}
        self._http: Optional[HTTPServer] = None
        self._running = False
        self._port = int(os.environ.get("RELAY_PORT", "8002"))

    def add_camera(self, cam_id: str, source, **kwargs) -> CameraSource:
        cam = CameraSource(cam_id, source, **kwargs)
        self.cameras[cam_id] = cam
        return cam

    def start(self):
        # Start all camera source threads
        for cam in self.cameras.values():
            cam.start()

        # Inject cameras into handler
        RelayHTTPHandler.cameras = self.cameras

        # Start HTTP server (threaded)
        import socketserver

        class ThreadedHTTPServer(
            socketserver.ThreadingMixIn, HTTPServer
        ):
            daemon_threads = True

        self._http = ThreadedHTTPServer(("0.0.0.0", self._port), RelayHTTPHandler)
        self._running = True

        t = threading.Thread(
            target=self._http.serve_forever,
            daemon=True,
            name="relay-http",
        )
        t.start()
        logger.info(
            f"[RelayServer] ✅ Listening on :{self._port} "
            f"cameras={list(self.cameras.keys())}"
        )

    def stop(self):
        self._running = False
        for cam in self.cameras.values():
            cam.stop()
        if self._http:
            self._http.shutdown()
        logger.info("[RelayServer] Stopped.")

    def run_forever(self):
        """Block until SIGTERM/SIGINT."""
        self.start()
        logger.info("[RelayServer] Running. Press Ctrl+C to stop.")

        def _shutdown(sig, _):
            logger.info(f"[RelayServer] Signal {sig} — stopping")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT,  _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        while self._running:
            time.sleep(1)


# ── Configuration & Entry Point ────────────────────────────────────────────────

def load_camera_config() -> List[dict]:
    """
    Load camera list from environment / config file.
    Falls back to a single demo camera if nothing is configured.
    """
    config_file = os.environ.get("RELAY_CONFIG", "/app/config/relay_cameras.json")
    cameras = []

    if Path(config_file).exists():
        try:
            with open(config_file) as f:
                cameras = json.load(f)
            logger.info(f"[Config] Loaded {len(cameras)} cameras from {config_file}")
            return cameras
        except Exception as e:
            logger.warning(f"[Config] Cannot load {config_file}: {e}")

    # Build from environment variables
    # Support CAMERA_0_SOURCE=rtsp://..., CAMERA_0_ID=CAM_001, etc.
    i = 0
    while True:
        src = os.environ.get(f"CAMERA_{i}_SOURCE")
        if src is None:
            break
        cameras.append({
            "id":     os.environ.get(f"CAMERA_{i}_ID",     f"CAM_{i:03d}"),
            "source": src,
            "fps":    int(os.environ.get(f"CAMERA_{i}_FPS", "15")),
        })
        i += 1

    # Single camera from CAMERA_SOURCE env
    if not cameras:
        src = os.environ.get("CAMERA_SOURCE", "demo")
        cameras.append({
            "id":     os.environ.get("CAMERA_ID", "CAM_001"),
            "source": src,
            "fps":    int(os.environ.get("TARGET_FPS", "15")),
        })
        logger.info(f"[Config] Using single camera: {src}")

    return cameras


def main():
    server = StreamRelayServer()

    camera_configs = load_camera_config()
    for c in camera_configs:
        server.add_camera(
            cam_id     = c.get("id", "CAM_001"),
            source     = c.get("source", "demo"),
            target_fps = c.get("fps", 15),
        )

    server.run_forever()


if __name__ == "__main__":
    main()
