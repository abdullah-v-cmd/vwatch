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
    Includes retry logic, queuing, and offline buffering.
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

    def _send(self, payload: Dict) -> bool:
        """Send single violation to backend."""
        try:
            import requests
            meta = payload["metadata"]
            files_dict = payload.get("files", {})

            # Send metadata
            response = requests.post(
                f"{self.base_url}/api/v1/violations",
                json=meta,
                headers=self._headers,
                timeout=10,
            )

            if response.status_code in (200, 201):
                violation_id = response.json().get("id")
                logger.info(f"[APIClient] Violation submitted: {violation_id}")

                # Upload files if present
                if violation_id and files_dict:
                    self._upload_files(violation_id, files_dict)
                return True
            else:
                logger.error(f"[APIClient] HTTP {response.status_code}: {response.text[:200]}")
                return False
        except ImportError:
            logger.warning("[APIClient] 'requests' not installed. Simulating send.")
            logger.info(f"[APIClient] SIMULATED SEND: {json.dumps(payload['metadata'], indent=2)}")
            return True
        except Exception as e:
            logger.error(f"[APIClient] Send error: {e}")
            return False

    def _upload_files(self, violation_id: str, files: Dict[str, str]):
        """Upload evidence files to backend."""
        try:
            import requests
            for file_type, file_path in files.items():
                if file_path and Path(file_path).exists():
                    with open(file_path, "rb") as f:
                        requests.post(
                            f"{self.base_url}/api/v1/violations/{violation_id}/files",
                            files={file_type: f},
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            timeout=30,
                        )
        except Exception as e:
            logger.error(f"[APIClient] File upload error: {e}")

    def _buffer_to_disk(self, payload: Dict):
        """Buffer violation to disk for later replay."""
        try:
            meta = payload.get("metadata", {})
            # Remove non-serializable items
            safe_meta = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))}
            with open(self.offline_buffer_path, "a") as f:
                f.write(json.dumps({"metadata": safe_meta, "files": payload.get("files", {})}) + "\n")
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
            # Clear buffer file
            open(self.offline_buffer_path, "w").close()
        except Exception as e:
            logger.error(f"[APIClient] Replay error: {e}")
