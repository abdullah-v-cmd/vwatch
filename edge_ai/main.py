"""
V-Watch Edge AI - Main Orchestrator
Entry point for the edge AI module
"""

import cv2
import time
import logging
import argparse
import json
import signal
import sys
import numpy as np
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("vwatch_edge.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("vwatch.main")

from stream_handler import StreamHandler
from detectors.vehicle_detector import VehicleDetector
from trackers.deepsort_tracker import DeepSORTTracker
from violations.speed_detector import DefaultSpeedEstimator
from violations.redlight_detector import RedLightViolationDetector, StopLine, SignalState
from violations.wrong_direction_detector import WrongDirectionDetector, LaneViolationDetector
from anpr.anpr_processor import ANPRProcessor
from evidence.evidence_generator import EvidenceGenerator
from utils.face_blurring import FaceBlurrer
from utils.api_client import ViolationAPIClient


def load_config(config_path: str) -> dict:
    """Load configuration from JSON file."""
    default_config = {
        "camera_id": "CAM_001",
        "location": "Main Street & 1st Ave",
        "source": 0,
        "fps": 15,
        "resolution": [1280, 720],
        "speed_limit_kmh": 60.0,
        "backend_url": "http://localhost:8000",
        "api_key": "",
        "output_dir": "evidence_store",
        "save_video_clips": False,
        "stop_lines": [
            {"start": [400, 400], "end": [900, 400], "direction": "horizontal"}
        ],
        "lanes": [],
        "blur_faces": True,
        "model_path": "yolov8n.pt",
        "device": "cpu",
        "display": True,
    }

    if Path(config_path).exists():
        with open(config_path, "r") as f:
            user_config = json.load(f)
        default_config.update(user_config)

    return default_config


class VWatchEdgeEngine:
    """
    Main V-Watch edge processing engine.
    Orchestrates all components: detection, tracking, violation analysis, evidence.
    """

    def __init__(self, config: dict):
        self.config = config
        self._running = False

        logger.info("=" * 60)
        logger.info("V-Watch Edge AI Engine Starting")
        logger.info(f"Camera: {config['camera_id']} | Location: {config['location']}")
        logger.info("=" * 60)

        # Initialize components
        self._init_components()

    def _init_components(self):
        cfg = self.config

        # 1. Stream handler
        self.stream = StreamHandler(
            source=cfg["source"],
            target_fps=cfg.get("fps", 15),
            resize=tuple(cfg.get("resolution", [1280, 720])),
        )

        # 2. Vehicle detector
        self.detector = VehicleDetector(
            model_path=cfg.get("model_path", "yolov8n.pt"),
            confidence_threshold=cfg.get("confidence_threshold", 0.5),
            device=cfg.get("device", "cpu"),
        )

        # 3. Multi-object tracker
        self.tracker = DeepSORTTracker(max_age=30, min_hits=3)

        # 4. Speed estimator
        self.speed_estimator = DefaultSpeedEstimator(
            speed_limit_kmh=cfg.get("speed_limit_kmh", 60.0),
            fps=cfg.get("fps", 15),
        )

        # 5. Red-light detector
        stop_lines = [
            StopLine(
                line_start=tuple(sl["start"]),
                line_end=tuple(sl["end"]),
                direction=sl.get("direction", "horizontal"),
            )
            for sl in cfg.get("stop_lines", [])
        ]
        self.redlight_detector = RedLightViolationDetector(
            stop_lines=stop_lines,
            signal_roi=cfg.get("signal_roi"),
        )

        # 6. Wrong direction detector
        self.wrong_direction_detector = WrongDirectionDetector(lanes=[])
        self.lane_violation_detector = LaneViolationDetector(lane_boundaries=[])

        # 7. ANPR
        self.anpr = ANPRProcessor()

        # 8. Evidence generator
        self.evidence_gen = EvidenceGenerator(
            output_dir=cfg.get("output_dir", "evidence_store"),
            camera_id=cfg["camera_id"],
            location=cfg["location"],
            save_video_clips=cfg.get("save_video_clips", False),
        )

        # 9. Face blurrer
        self.face_blurrer = FaceBlurrer() if cfg.get("blur_faces", True) else None

        # 10. API client
        self.api_client = ViolationAPIClient(
            base_url=cfg.get("backend_url", "http://localhost:8000"),
            api_key=cfg.get("api_key", ""),
        )

        # Statistics
        self._stats = {
            "frames_processed": 0,
            "violations_detected": 0,
            "start_time": time.time(),
        }

        logger.info("[Engine] All components initialized.")

    def _check_violations(self, tracks, frame, timestamp):
        """Check all active tracks for violations."""
        violations = []

        for track in tracks:
            # Extract vehicle crop
            x1, y1, x2, y2 = track.bbox
            h, w = frame.shape[:2]
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(w, int(x2)), min(h, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            vehicle_crop = frame[y1:y2, x1:x2]

            # ANPR
            plate_info = self.anpr.process(vehicle_crop, track_id=track.track_id)

            # Speed check
            is_speeding, speed_kmh = self.speed_estimator.is_speeding(track, timestamp)
            if is_speeding:
                violations.append({
                    "track": track,
                    "type": "SPEEDING",
                    "speed": speed_kmh,
                    "plate_info": plate_info,
                    "vehicle_crop": vehicle_crop,
                })

            # Red-light check
            is_redlight, line_id = self.redlight_detector.check_violation(track, frame)
            if is_redlight:
                violations.append({
                    "track": track,
                    "type": "RED_LIGHT",
                    "speed": 0.0,
                    "plate_info": plate_info,
                    "additional": {"stop_line": line_id},
                    "vehicle_crop": vehicle_crop,
                })

            # Wrong direction check
            is_wrong, lane_id = self.wrong_direction_detector.check_violation(track)
            if is_wrong:
                violations.append({
                    "track": track,
                    "type": "WRONG_DIRECTION",
                    "speed": 0.0,
                    "plate_info": plate_info,
                    "additional": {"lane": lane_id},
                    "vehicle_crop": vehicle_crop,
                })

            # Lane violation check
            is_lane_vio, boundary_id = self.lane_violation_detector.check_violation(track)
            if is_lane_vio:
                violations.append({
                    "track": track,
                    "type": "LANE_VIOLATION",
                    "speed": 0.0,
                    "plate_info": plate_info,
                    "additional": {"boundary": boundary_id},
                    "vehicle_crop": vehicle_crop,
                })

        return violations

    def _process_violation(self, vio: dict, frame: np.ndarray):
        """Generate evidence and submit violation."""
        track = vio["track"]
        evidence = self.evidence_gen.generate(
            frame=frame,
            track=track,
            violation_type=vio["type"],
            plate_info=vio["plate_info"],
            speed=vio.get("speed", 0.0),
            additional_data=vio.get("additional", {}),
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

    def _draw_hud(self, frame: np.ndarray, tracks, fps: float) -> np.ndarray:
        """Draw HUD overlay on frame."""
        # Draw tracks
        for track in tracks:
            x1, y1, x2, y2 = [int(v) for v in track.bbox]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            label = f"ID:{track.track_id} {track.class_name}"
            if hasattr(track, 'speed_history') and track.speed_history:
                label += f" {sum(track.speed_history)/len(track.speed_history):.0f}km/h"
            cv2.putText(frame, label, (x1, max(y1 - 5, 15)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

        # Red-light overlay
        frame = self.redlight_detector.draw_overlay(frame)

        # Stats HUD
        uptime = time.time() - self._stats["start_time"]
        stats_text = [
            f"V-Watch | CAM: {self.config['camera_id']}",
            f"FPS: {fps:.1f} | Tracks: {len(tracks)}",
            f"Violations: {self._stats['violations_detected']}",
            f"Uptime: {uptime:.0f}s",
        ]
        for i, txt in enumerate(stats_text):
            cv2.putText(frame, txt, (10, 20 + i * 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        return frame

    def run(self):
        """Main processing loop."""
        self.stream.start()
        self.api_client.start()
        self._running = True

        # Notify backend that camera is active
        self.api_client.update_camera_status(
            camera_id=self.config["camera_id"],
            status="active",
            message="Edge AI engine started",
        )

        logger.info("[Engine] Processing loop started. Press 'q' to quit.")

        frame_count = 0
        fps_timer = time.time()
        current_fps = 0.0

        display = self.config.get("display", True)

        try:
            for timestamp, frame in self.stream.frames():
                if not self._running:
                    break

                frame_count += 1
                self._stats["frames_processed"] += 1

                # Feed frame to video buffer
                self.evidence_gen.feed_frame(frame)

                # Privacy: blur faces
                if self.face_blurrer:
                    frame = self.face_blurrer.blur_faces(frame)

                # Object detection
                detections = self.detector.detect(frame)

                # Multi-object tracking
                tracks = self.tracker.update(detections, frame)

                # Update signal state
                self.redlight_detector.update_signal(frame)

                # Violation detection
                violations = self._check_violations(tracks, frame, timestamp)

                # Process violations
                for vio in violations:
                    self._process_violation(vio, frame)

                # FPS calculation
                if frame_count % 30 == 0:
                    elapsed = time.time() - fps_timer
                    current_fps = 30.0 / elapsed if elapsed > 0 else 0
                    fps_timer = time.time()

                # Display
                if display:
                    hud_frame = self._draw_hud(frame.copy(), tracks, current_fps)
                    cv2.imshow("V-Watch Edge AI", hud_frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        break
                    elif key == ord("r"):
                        # Toggle red light manually
                        current = self.redlight_detector._current_signal
                        new_state = SignalState.GREEN if current == SignalState.RED else SignalState.RED
                        self.redlight_detector.set_signal_state(new_state)
                        logger.info(f"[Engine] Signal toggled to {new_state}")

        except KeyboardInterrupt:
            logger.info("[Engine] Interrupted by user.")
        finally:
            self.stop()

    def stop(self):
        """Gracefully stop all components."""
        self._running = False
        # Notify backend that camera is stopping
        try:
            self.api_client.update_camera_status(
                camera_id=self.config["camera_id"],
                status="idle",
                message="Edge AI engine stopped",
            )
        except Exception:
            pass
        self.stream.stop()
        self.api_client.stop()
        cv2.destroyAllWindows()
        logger.info(f"[Engine] Stopped. Stats: {self._stats}")


def main():
    parser = argparse.ArgumentParser(description="V-Watch Edge AI Module")
    parser.add_argument("--config", default="config/edge_config.json",
                        help="Path to configuration file")
    parser.add_argument("--source", default=None,
                        help="Override video source (0 for webcam, RTSP URL, or file path)")
    parser.add_argument("--no-display", action="store_true", help="Disable display window")
    args = parser.parse_args()

    config = load_config(args.config)

    if args.source is not None:
        try:
            config["source"] = int(args.source)
        except ValueError:
            config["source"] = args.source

    if args.no_display:
        config["display"] = False

    engine = VWatchEdgeEngine(config)

    # Handle SIGINT/SIGTERM
    def shutdown(sig, frame):
        logger.info("[Engine] Shutdown signal received.")
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    engine.run()


if __name__ == "__main__":
    main()
