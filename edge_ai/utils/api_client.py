"""
V-Watch Edge AI - Backend API Client
Sends violations to centralized management backend
"""

import json
import logging
import time
import hashlib
import os
from typing import Optional, Dict, List
from pathlib import Path
from queue import Queue
import threading

logger = logging.getLogger(__name__)


class ViolationAPIClient:
    """
    HTTP client for sending violations to V-Watch backend.
    Includes retry logic, queuing, offline buffering,
    and live monitoring WebSocket broadcast.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: str = "",
        max_retries: int = 3,
        retry_delay: float = 2.0,
        offline_buffer_path: str = "offline_buffer.jsonl",
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.offline_buffer_path = offline_buffer_path
        self._queue: Queue = Queue(maxsize=500)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def start(self):
        """Start background sender thread."""
        self._running = True
        self._thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._thread.start()
        # Replay any offline-buffered violations
        self._replay_offline_buffer()
        logger.info("[APIClient] Started violation sender.")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def submit_violation(self, evidence_metadata: Dict, files: Dict[str, str] = None):
        """Queue a violation for submission."""
        payload = {
            "metadata": evidence_metadata,
            "files": files or {},
        }
        try:
            self._queue.put_nowait(payload)
        except Exception:
            logger.warning("[APIClient] Queue full. Buffering to disk.")
            self._buffer_to_disk(payload)

    def _sender_loop(self):
        """Background thread: sends queued violations."""
        while self._running:
            try:
                payload = self._queue.get(timeout=1.0)
                success = self._send_with_retry(payload)
                if not success:
                    self._buffer_to_disk(payload)
            except Exception:
                pass

    def _send_with_retry(self, payload: Dict) -> bool:
        """Send violation with retry logic."""
        for attempt in range(self.max_retries):
            try:
                success = self._send(payload)
                if success:
                    return True
            except Exception as e:
                logger.warning(f"[APIClient] Send attempt {attempt+1} failed: {e}")
            time.sleep(self.retry_delay * (attempt + 1))
        return False

    def _build_violation_payload(self, meta: Dict) -> Dict:
        """
        Build the ViolationCreate-compatible payload from evidence metadata.
        Maps edge AI evidence fields to backend schema fields.
        """
        import uuid

        # Support both old and new metadata formats
        evidence_id = meta.get("evidence_id") or meta.get("id") or str(uuid.uuid4())
        vehicle_id = (
            meta.get("vehicle_id") or
            meta.get("track_id") or
            f"TRACK_{meta.get('camera_id', 'CAM')}_{int(time.time())}"
        )
        plate_number = (
            meta.get("plate_number") or
            meta.get("plate") or
            "UNKNOWN"
        )
        violation_type = (
            meta.get("violation_type") or
            meta.get("type") or
            "SPEEDING"
        )
        location = (
            meta.get("location") or
            meta.get("camera_location") or
            "Unknown Location"
        )
        camera_id = (
            meta.get("camera_id") or
            meta.get("cam_id") or
            "CAM_UNKNOWN"
        )
        violation_time = (
            meta.get("violation_time") or
            meta.get("timestamp") or
            meta.get("detected_at") or
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )
        confidence = float(meta.get("confidence", 0.0))
        speed = meta.get("speed_recorded") or meta.get("speed_kmh") or meta.get("speed")
        speed_limit = meta.get("speed_limit") or meta.get("speed_limit_kmh")
        vehicle_type = meta.get("vehicle_type") or meta.get("class_name")

        return {
            "evidence_id": str(evidence_id),
            "vehicle_id": str(vehicle_id),
            "plate_number": str(plate_number).upper(),
            "vehicle_type": str(vehicle_type) if vehicle_type else None,
            "violation_type": str(violation_type).upper(),
            "speed_recorded": float(speed) if speed is not None else None,
            "speed_limit": float(speed_limit) if speed_limit is not None else None,
            "location": str(location),
            "camera_id": str(camera_id),
            "violation_time": str(violation_time),
            "confidence": confidence,
            "frame_sha256": meta.get("frame_sha256"),
            "plate_sha256": meta.get("plate_sha256"),
            "video_sha256": meta.get("video_sha256"),
            "metadata_sha256": meta.get("metadata_sha256"),
            "extra_data": meta.get("extra_data") or {},
        }

    def _send(self, payload: Dict) -> bool:
        """Send single violation to backend."""
        try:
            import requests
            meta = payload["metadata"]
            files_dict = payload.get("files", {})

            # Build proper backend payload
            violation_payload = self._build_violation_payload(meta)

            # Step 1: POST violation metadata to create record
            response = requests.post(
                f"{self.base_url}/api/v1/violations",
                json=violation_payload,
                headers=self._headers,
                timeout=15,
            )

            if response.status_code in (200, 201):
                violation_id = response.json().get("id")
                logger.info(f"[APIClient] Violation created: ID={violation_id} type={violation_payload['violation_type']} plate={violation_payload['plate_number']}")

                # Step 2: Upload evidence files if present
                if violation_id and files_dict:
                    self._upload_files(violation_id, files_dict)

                # Step 3: Also broadcast to live monitoring endpoint
                self._broadcast_live(violation_payload, violation_id)

                return True

            elif response.status_code == 409:
                # Duplicate evidence_id - already submitted, treat as success
                logger.info(f"[APIClient] Duplicate violation (already submitted): {violation_payload['evidence_id']}")
                return True

            else:
                logger.error(
                    f"[APIClient] HTTP {response.status_code}: "
                    f"{response.text[:300]} | payload={json.dumps(violation_payload, default=str)[:200]}"
                )
                return False

        except ImportError:
            # requests not installed - simulate
            violation_payload = self._build_violation_payload(payload["metadata"])
            logger.warning("[APIClient] 'requests' not installed. Simulating send.")
            logger.info(
                f"[APIClient] SIMULATED: type={violation_payload['violation_type']} "
                f"plate={violation_payload['plate_number']} cam={violation_payload['camera_id']}"
            )
            return True
        except Exception as e:
            logger.error(f"[APIClient] Send error: {e}")
            return False

    def _broadcast_live(self, violation_payload: Dict, violation_id: Optional[int]):
        """Notify live monitoring endpoint about the new violation."""
        try:
            import requests
            live_payload = {
                "camera_id": violation_payload.get("camera_id", "UNKNOWN"),
                "violation_type": violation_payload.get("violation_type", "UNKNOWN"),
                "plate_number": violation_payload.get("plate_number", "UNKNOWN"),
                "confidence": violation_payload.get("confidence", 0.0),
                "speed": violation_payload.get("speed_recorded"),
                "location": violation_payload.get("location", ""),
                "timestamp": violation_payload.get("violation_time", ""),
            }
            requests.post(
                f"{self.base_url}/api/v1/live/violations/report",
                json=live_payload,
                headers=self._headers,
                timeout=5,
            )
        except Exception as e:
            logger.debug(f"[APIClient] Live broadcast error (non-critical): {e}")

    def _upload_files(self, violation_id: int, files: Dict[str, str]):
        """Upload evidence files to backend."""
        try:
            import requests
            files_to_upload = {}
            for file_type, file_path in files.items():
                if file_path and Path(file_path).exists():
                    files_to_upload[file_type] = open(file_path, "rb")

            if files_to_upload:
                try:
                    requests.post(
                        f"{self.base_url}/api/v1/violations/{violation_id}/files",
                        files=files_to_upload,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        timeout=30,
                    )
                    logger.info(f"[APIClient] Files uploaded for violation {violation_id}")
                finally:
                    for f in files_to_upload.values():
                        try:
                            f.close()
                        except Exception:
                            pass
        except Exception as e:
            logger.error(f"[APIClient] File upload error: {e}")

    def _buffer_to_disk(self, payload: Dict):
        """Buffer violation to disk for later replay."""
        try:
            meta = payload.get("metadata", {})
            safe_meta = {
                k: v for k, v in meta.items()
                if isinstance(v, (str, int, float, bool, list, dict, type(None)))
            }
            with open(self.offline_buffer_path, "a") as f:
                f.write(json.dumps({
                    "metadata": safe_meta,
                    "files": payload.get("files", {}),
                }) + "\n")
        except Exception as e:
            logger.error(f"[APIClient] Buffer write error: {e}")

    def _replay_offline_buffer(self):
        """Replay previously buffered violations."""
        if not Path(self.offline_buffer_path).exists():
            return
        try:
            replayed = 0
            with open(self.offline_buffer_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        payload = json.loads(line)
                        self._queue.put_nowait(payload)
                        replayed += 1
            if replayed > 0:
                logger.info(f"[APIClient] Replaying {replayed} offline-buffered violations.")
            # Clear buffer file after loading
            open(self.offline_buffer_path, "w").close()
        except Exception as e:
            logger.error(f"[APIClient] Replay error: {e}")

    def update_camera_status(self, camera_id: str, status: str, fps: float = None, message: str = None):
        """Notify backend about camera status changes."""
        try:
            import requests
            requests.post(
                f"{self.base_url}/api/v1/live/cameras/status",
                json={
                    "camera_id": camera_id,
                    "status": status,
                    "fps": fps,
                    "message": message,
                },
                headers=self._headers,
                timeout=5,
            )
        except Exception as e:
            logger.debug(f"[APIClient] Camera status update error (non-critical): {e}")
