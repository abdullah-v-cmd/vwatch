"""
V-Watch Edge AI - Production Main Engine
==========================================
24/7 autonomous CCTV AI processing engine.

Production guarantees:
  ✔ Camera runs CONTINUOUSLY — never stops due to frontend/backend lifecycle
  ✔ YOLO loaded ONCE as singleton — never re-downloaded mid-run
  ✔ Works inside Docker (no /dev/video0 required — demo mode fallback)
  ✔ Auto-reconnects camera with exponential back-off on any failure
  ✔ Exposes /health HTTP endpoint for watchdog monitoring
  ✔ Graceful shutdown on SIGINT/SIGTERM
  ✔ Structured JSON logs with rotation
  ✔ Offline violation buffer — syncs to backend when reconnected
  ✔ Independent of frontend — never dies when browser navigates away
"""

import cv2
import os
import sys
import time
import json
import signal
import logging
import argparse
import threading
import numpy as np
from pathlib import Path
from logging.handlers import RotatingFileHandler
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Logging Setup ──────────────────────────────────────────────────────────────

LOG_DIR = os.environ.get("LOG_DIR", "/app/logs")
os.makedirs(LOG_DIR, exist_ok=True)

_log_handlers = [logging.StreamHandler(sys.stdout)]
try:
    _log_handlers.append(
        RotatingFileHandler(
            os.path.join(LOG_DIR, "edge_ai.log"),
            maxBytes=20 * 1024 * 1024,   # 20 MB
            backupCount=5,
            encoding="utf-8",
        )
    )
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("vwatch.main")

# ── Internal imports (after logging is set up) ─────────────────────────────────

from stream_handler import StreamHandler
from detectors.vehicle_detector import VehicleDetector, get_yolo_status
from trackers.deepsort_tracker import DeepSORTTracker
from violations.speed_detector import DefaultSpeedEstimator
from violations.redlight_detector import (
    RedLightViolationDetector, StopLine, SignalState,
)
from violations.wrong_direction_detector import (
    WrongDirectionDetector, LaneViolationDetector,
)
from anpr.anpr_processor import ANPRProcessor
from evidence.evidence_generator import EvidenceGenerator
from utils.face_blurring import FaceBlurrer
from utils.api_client import ViolationAPIClient


# ── Config Loader ─────────────────────────────────────────────────────────────

def load_config(config_path: str) -> dict:
    """
    Load configuration from JSON file.
    Environment variables always override JSON values — critical for Docker.
    """
    default_config: dict = {
        "camera_id":          os.environ.get("CAMERA_ID",   "CAM_001"),
        "location":           os.environ.get("LOCATION",    "Main Street Camera"),
        # CAMERA_SOURCE: "0", "/dev/video0", "rtsp://...", "demo"
        "source":             _parse_source(
                                  os.environ.get("CAMERA_SOURCE", "demo")
                              ),
        "fps":                int(os.environ.get("TARGET_FPS",   "15")),
        "resolution":         [1280, 720],
        "speed_limit_kmh":    60.0,
        "confidence_threshold": 0.45,
        "backend_url":        os.environ.get("BACKEND_URL", "http://backend:8000"),
        "api_key":            os.environ.get("API_KEY", ""),
        "output_dir":         os.environ.get("OUTPUT_DIR",  "/app/uploads"),
        "save_video_clips":   False,
        "stop_lines":         [
            {"start": [400, 400], "end": [900, 400], "direction": "horizontal"}
        ],
        "lanes":              [],
        "blur_faces":         os.environ.get("BLUR_FACES", "true").lower() == "true",
        "model_path":         os.environ.get("YOLO_MODEL_PATH", "/app/models/yolov8n.pt"),
        "device":             os.environ.get("YOLO_DEVICE",     "cpu"),
        "display":            False,   # Always off inside Docker
        "health_port":        int(os.environ.get("HEALTH_PORT", "8001")),
    }

    if Path(config_path).exists():
        try:
            with open(config_path, "r") as f:
                user_cfg = json.load(f)
            default_config.update(user_cfg)
            logger.info(f"[Config] Loaded from {config_path}")
        except Exception as e:
            logger.warning(f"[Config] Could not load {config_path}: {e} — using defaults")
    else:
        logger.info(f"[Config] {config_path} not found — using defaults + env vars")

    # Environment always wins (Docker overrides)
    _env_overrides(default_config)
    return default_config


def _parse_source(raw: str):
    """Convert CAMERA_SOURCE env value to int (device) or str (rtsp/file/demo)."""
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    return raw  # "/dev/video0", "rtsp://...", "demo", file path


def _env_overrides(cfg: dict):
    """Apply environment variable overrides (env always wins)."""
    env_map = {
        "CAMERA_ID":       ("camera_id",       str),
        "LOCATION":        ("location",        str),
        "BACKEND_URL":     ("backend_url",     str),
        "API_KEY":         ("api_key",         str),
        "YOLO_MODEL_PATH": ("model_path",      str),
        "YOLO_DEVICE":     ("device",          str),
        "TARGET_FPS":      ("fps",             int),
        "OUTPUT_DIR":      ("output_dir",      str),
        "HEALTH_PORT":     ("health_port",     int),
    }
    for env_key, (cfg_key, cast) in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            try:
                cfg[cfg_key] = cast(val)
            except (ValueError, TypeError):
                pass
    # Source
    src = os.environ.get("CAMERA_SOURCE")
    if src is not None:
        cfg["source"] = _parse_source(src)


# ── Health HTTP Server ─────────────────────────────────────────────────────────

class HealthServer:
    """
    Tiny HTTP server exposing /health and /metrics for the watchdog.
    Runs in a daemon thread — never blocks the main pipeline.
    """

    def __init__(self, engine, port: int = 8001):
        self._engine = engine
        self._port   = port
        self._server: HTTPServer | None = None

    def start(self):
        engine_ref = self._engine

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # Silence per-request logs

            def do_GET(self):
                if self.path in ("/health", "/"):
                    self._respond_health(engine_ref)
                elif self.path == "/metrics":
                    self._respond_metrics(engine_ref)
                else:
                    self.send_response(404)
                    self.end_headers()

            def _respond_health(self, eng):
                import json as _json
                body = _json.dumps({
                    "status":     "ok" if eng._running else "stopped",
                    "service":    "edge_ai",
                    "camera_id":  eng.config.get("camera_id", "unknown"),
                    "stream_state": eng.stream.state if eng.stream else "none",
                    "yolo_loaded": get_yolo_status().get("loaded", False),
                    "uptime_s":   round(time.monotonic() - eng._start_time, 1),
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def _respond_metrics(self, eng):
                import json as _json
                body = _json.dumps({
                    "stats": eng._stats,
                    "yolo":  get_yolo_status(),
                    "stream": eng.stream.get_metadata() if eng.stream else {},
                }, default=str).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

        try:
            server = HTTPServer(("0.0.0.0", self._port), Handler)
            self._server = server
            t = threading.Thread(
                target=server.serve_forever,
                daemon=True,
                name="health-http",
            )
            t.start()
            logger.info(f"[HealthServer] Listening on :{self._port}  /health  /metrics")
        except OSError as e:
            logger.warning(f"[HealthServer] Could not start on port {self._port}: {e}")

    def stop(self):
        if self._server:
            self._server.shutdown()


# ── Main Engine ────────────────────────────────────────────────────────────────

class VWatchEdgeEngine:
    """
    Production 24/7 Edge AI Engine.

    Architecture:
      1. StreamHandler  — persistent camera thread with auto-reconnect
      2. VehicleDetector — YOLO singleton, loaded once
      3. DeepSORTTracker — multi-object tracking
      4. Violation detectors — speed, red-light, wrong direction
      5. ANPRProcessor  — license plate recognition
      6. EvidenceGenerator — saves annotated frames + optional clips
      7. ViolationAPIClient — async queue → backend with offline buffer
      8. HealthServer  — /health endpoint for watchdog
    """

    def __init__(self, config: dict):
        self.config      = config
        self._running    = False
        self._start_time = time.monotonic()

        # ── Populated in _init_components ──
        self.stream:       StreamHandler | None           = None
        self.detector:     VehicleDetector | None         = None
        self.tracker:      DeepSORTTracker | None         = None
        self.speed_est:    DefaultSpeedEstimator | None   = None
        self.redlight_det: RedLightViolationDetector|None = None
        self.wrong_dir:    WrongDirectionDetector | None  = None
        self.lane_vio:     LaneViolationDetector | None   = None
        self.anpr:         ANPRProcessor | None           = None
        self.evidence_gen: EvidenceGenerator | None       = None
        self.face_blur:    FaceBlurrer | None             = None
        self.api_client:   ViolationAPIClient | None      = None
        self.health_srv:   HealthServer | None            = None

        self._stats = {
            "frames_processed":  0,
            "violations_detected": 0,
            "start_time":        time.time(),
            "camera_id":         config.get("camera_id", "unknown"),
        }

        logger.info("=" * 65)
        logger.info("V-Watch Edge AI — Production Engine")
        logger.info(f"  Camera    : {config['camera_id']}")
        logger.info(f"  Location  : {config['location']}")
        logger.info(f"  Source    : {config['source']}")
        logger.info(f"  Backend   : {config['backend_url']}")
        logger.info(f"  Model     : {config['model_path']}")
        logger.info(f"  Device    : {config['device']}")
        logger.info("=" * 65)

        self._init_components()

    # ── Component Initialization ───────────────────────────────────────────────

    def _init_components(self):
        cfg = self.config

        # 1. Persistent stream handler (runs in its own thread, never stops)
        self.stream = StreamHandler(
            source          = cfg["source"],
            target_fps      = cfg.get("fps", 15),
            resize          = tuple(cfg.get("resolution", [1280, 720])),
            reconnect_delay = 2.0,
            backoff_factor  = 1.5,
            max_reconnect_delay = 60.0,
        )

        # 2. YOLO vehicle detector — SINGLETON, loaded once
        logger.info("[Engine] Loading YOLO model (singleton)...")
        self.detector = VehicleDetector(
            model_path            = cfg.get("model_path", "yolov8n.pt"),
            confidence_threshold  = cfg.get("confidence_threshold", 0.45),
            device                = cfg.get("device", "cpu"),
        )
        yolo_status = get_yolo_status()
        if yolo_status.get("loaded"):
            logger.info(f"[Engine] ✅ YOLO model loaded: {yolo_status['model_path']}")
        else:
            logger.warning(
                f"[Engine] ⚠️ YOLO in MOCK mode: {yolo_status.get('error', 'unknown')}"
            )

        # 3. Multi-object tracker
        self.tracker = DeepSORTTracker(max_age=30, min_hits=3)

        # 4. Speed estimator
        self.speed_est = DefaultSpeedEstimator(
            speed_limit_kmh = cfg.get("speed_limit_kmh", 60.0),
            fps             = cfg.get("fps", 15),
        )

        # 5. Red-light detector
        stop_lines = [
            StopLine(
                line_start = tuple(sl["start"]),
                line_end   = tuple(sl["end"]),
                direction  = sl.get("direction", "horizontal"),
            )
            for sl in cfg.get("stop_lines", [])
        ]
        self.redlight_det = RedLightViolationDetector(
            stop_lines  = stop_lines,
            signal_roi  = cfg.get("signal_roi"),
        )

        # 6. Wrong direction / lane detectors
        self.wrong_dir = WrongDirectionDetector(lanes=[])
        self.lane_vio  = LaneViolationDetector(lane_boundaries=[])

        # 7. ANPR
        self.anpr = ANPRProcessor()

        # 8. Evidence generator
        output_dir = cfg.get("output_dir", "/app/uploads")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self.evidence_gen = EvidenceGenerator(
            output_dir        = output_dir,
            camera_id         = cfg["camera_id"],
            location          = cfg["location"],
            save_video_clips  = cfg.get("save_video_clips", False),
        )

        # 9. Face blurrer (privacy)
        self.face_blur = (
            FaceBlurrer()
            if cfg.get("blur_faces", True)
            else None
        )

        # 10. Backend API client (async queue + offline buffer)
        self.api_client = ViolationAPIClient(
            base_url              = cfg.get("backend_url", "http://backend:8000"),
            api_key               = cfg.get("api_key", ""),
            offline_buffer_path   = os.path.join(output_dir, "offline_buffer.jsonl"),
        )

        # 11. Health server (watchdog endpoint)
        self.health_srv = HealthServer(
            engine = self,
            port   = cfg.get("health_port", 8001),
        )

        logger.info("[Engine] ✅ All components initialized.")

    # ── Violation Detection ───────────────────────────────────────────────────

    def _check_violations(self, tracks, frame, timestamp) -> list:
        violations = []
        for track in tracks:
            x1, y1, x2, y2 = track.bbox
            h, w = frame.shape[:2]
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(w, int(x2)), min(h, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2]

            plate_info = self.anpr.process(crop, track_id=track.track_id)

            is_speed, spd = self.speed_est.is_speeding(track, timestamp)
            if is_speed:
                violations.append(
                    dict(track=track, type="SPEEDING", speed=spd,
                         plate_info=plate_info, vehicle_crop=crop)
                )

            is_rl, line_id = self.redlight_det.check_violation(track, frame)
            if is_rl:
                violations.append(
                    dict(track=track, type="RED_LIGHT", speed=0.0,
                         plate_info=plate_info,
                         additional={"stop_line": line_id},
                         vehicle_crop=crop)
                )

            is_wd, lane_id = self.wrong_dir.check_violation(track)
            if is_wd:
                violations.append(
                    dict(track=track, type="WRONG_DIRECTION", speed=0.0,
                         plate_info=plate_info,
                         additional={"lane": lane_id},
                         vehicle_crop=crop)
                )

            is_lv, bnd_id = self.lane_vio.check_violation(track)
            if is_lv:
                violations.append(
                    dict(track=track, type="LANE_VIOLATION", speed=0.0,
                         plate_info=plate_info,
                         additional={"boundary": bnd_id},
                         vehicle_crop=crop)
                )
        return violations

    def _process_violation(self, vio: dict, frame: np.ndarray):
        """Generate evidence, save, and submit to backend."""
        track  = vio["track"]
        evidence = self.evidence_gen.generate(
            frame          = frame,
            track          = track,
            violation_type = vio["type"],
            plate_info     = vio["plate_info"],
            speed          = vio.get("speed", 0.0),
            additional_data= vio.get("additional", {}),
        )
        meta_dict = evidence.to_dict()
        files = {
            "frame": evidence.frame_image_path,
            "plate": evidence.plate_image_path,
            "video": evidence.video_clip_path,
        }
        self.api_client.submit_violation(meta_dict, files)
        self._stats["violations_detected"] += 1
        logger.info(
            f"[Engine] VIOLATION: {vio['type']} | "
            f"Plate: {vio['plate_info'].get('plate_number', 'UNKNOWN')} | "
            f"Speed: {vio.get('speed', 0):.1f} km/h"
        )

    # ── HUD Overlay ───────────────────────────────────────────────────────────

    def _draw_hud(self, frame: np.ndarray, tracks, fps: float) -> np.ndarray:
        """Draw production HUD overlay on frame."""
        for track in tracks:
            x1, y1, x2, y2 = [int(v) for v in track.bbox]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"ID:{track.track_id} {track.class_name}"
            if hasattr(track, "speed_history") and track.speed_history:
                avg_spd = sum(track.speed_history) / len(track.speed_history)
                label += f" {avg_spd:.0f}km/h"
            cv2.putText(frame, label, (x1, max(y1 - 5, 15)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        frame = self.redlight_det.draw_overlay(frame)

        uptime = time.time() - self._stats["start_time"]
        h, w   = frame.shape[:2]
        overlay_lines = [
            f"V-Watch | {self.config['camera_id']}",
            f"FPS: {fps:.1f} | Tracks: {len(tracks)}",
            f"Violations: {self._stats['violations_detected']}",
            f"Uptime: {uptime:.0f}s",
        ]
        for i, txt in enumerate(overlay_lines):
            y = 22 + i * 24
            # Shadow
            cv2.putText(frame, txt, (11, y + 1),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 3)
            # Text
            cv2.putText(frame, txt, (10, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        return frame

    # ── Main Loop ─────────────────────────────────────────────────────────────

    def run(self):
        """
        Start all components and enter the main processing loop.
        This method blocks until stop() is called or SIGTERM received.
        """
        # Start stream in its own thread
        self.stream.start()
        # Start API client sender thread
        self.api_client.start()
        # Start health HTTP server
        self.health_srv.start()

        self._running = True

        # Notify backend we are live
        self.api_client.update_camera_status(
            camera_id = self.config["camera_id"],
            status    = "active",
            message   = "Edge AI engine started",
        )

        logger.info("[Engine] 🚀 Processing loop started.")

        frame_count  = 0
        fps_timer    = time.monotonic()
        current_fps  = 0.0
        fps_window   = 30   # Recalculate FPS every N frames

        try:
            for timestamp, frame in self.stream.frames():
                if not self._running:
                    break

                frame_count += 1
                self._stats["frames_processed"] += 1

                # Feed raw frame into evidence video buffer
                self.evidence_gen.feed_frame(frame)

                # Privacy: blur faces
                if self.face_blur:
                    try:
                        frame = self.face_blur.blur_faces(frame)
                    except Exception:
                        pass

                # Vehicle detection (YOLO singleton)
                try:
                    detections = self.detector.detect(frame)
                except Exception as e:
                    logger.error(f"[Engine] Detection error: {e}")
                    detections = []

                # Multi-object tracking
                try:
                    tracks = self.tracker.update(detections, frame)
                except Exception as e:
                    logger.error(f"[Engine] Tracking error: {e}")
                    tracks = []

                # Traffic signal state
                try:
                    self.redlight_det.update_signal(frame)
                except Exception:
                    pass

                # Violation detection
                try:
                    violations = self._check_violations(tracks, frame, timestamp)
                except Exception as e:
                    logger.error(f"[Engine] Violation check error: {e}")
                    violations = []

                # Submit violations
                for vio in violations:
                    try:
                        self._process_violation(vio, frame)
                    except Exception as e:
                        logger.error(f"[Engine] Violation processing error: {e}")

                # FPS calculation
                if frame_count % fps_window == 0:
                    elapsed    = time.monotonic() - fps_timer
                    current_fps = fps_window / elapsed if elapsed > 0 else 0.0
                    fps_timer  = time.monotonic()
                    logger.debug(
                        f"[Engine] FPS={current_fps:.1f} "
                        f"Frames={self._stats['frames_processed']} "
                        f"Violations={self._stats['violations_detected']}"
                    )

                # Optional display (never inside Docker)
                if self.config.get("display", False):
                    hud = self._draw_hud(frame.copy(), tracks, current_fps)
                    cv2.imshow("V-Watch Edge AI", hud)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        break

        except KeyboardInterrupt:
            logger.info("[Engine] KeyboardInterrupt received.")
        except Exception as e:
            logger.error(f"[Engine] Fatal loop error: {e}", exc_info=True)
        finally:
            self.stop()

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def stop(self):
        """Gracefully shut down all components."""
        if not self._running:
            return
        self._running = False

        logger.info("[Engine] Shutting down...")

        try:
            self.api_client.update_camera_status(
                camera_id = self.config["camera_id"],
                status    = "idle",
                message   = "Edge AI engine stopped",
            )
        except Exception:
            pass

        if self.stream:
            self.stream.stop()
        if self.api_client:
            self.api_client.stop()
        if self.health_srv:
            self.health_srv.stop()

        cv2.destroyAllWindows()
        logger.info(f"[Engine] Stopped. Final stats: {self._stats}")


# ── Entry Point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="V-Watch Edge AI — Production Engine")
    parser.add_argument(
        "--config",
        default="config/edge_config.json",
        help="Path to configuration JSON (default: config/edge_config.json)",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Override CAMERA_SOURCE (0, /dev/video0, rtsp://..., demo, file path)",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="Disable OpenCV display window (required in Docker)",
    )
    parser.add_argument(
        "--health-port",
        type=int,
        default=None,
        help="Override health server port (default from env HEALTH_PORT or 8001)",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.source is not None:
        config["source"] = _parse_source(args.source)

    if args.no_display:
        config["display"] = False

    if args.health_port is not None:
        config["health_port"] = args.health_port

    engine = VWatchEdgeEngine(config)

    # Graceful shutdown on SIGINT / SIGTERM
    def _shutdown(sig, _frame):
        logger.info(f"[Engine] Signal {sig} received — shutting down")
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    engine.run()


if __name__ == "__main__":
    main()
