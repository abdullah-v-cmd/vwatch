"""
V-Watch Edge AI - Video Stream Handler
Handles RTSP, webcam, and file-based video ingestion
"""

import cv2
import time
import logging
import threading
from queue import Queue, Full
from typing import Optional, Generator, Tuple

logger = logging.getLogger(__name__)


class StreamHandler:
    """Thread-safe video stream handler supporting RTSP, webcam, and file input."""

    def __init__(
        self,
        source: str | int,
        frame_queue_size: int = 30,
        target_fps: int = 15,
        resize: Tuple[int, int] = (1280, 720),
        reconnect_attempts: int = 5,
        reconnect_delay: float = 2.0,
    ):
        self.source = source
        self.frame_queue: Queue = Queue(maxsize=frame_queue_size)
        self.target_fps = target_fps
        self.resize = resize
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_delay = reconnect_delay

        self._cap: Optional[cv2.VideoCapture] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._frame_interval = 1.0 / target_fps
        self._lock = threading.Lock()

    def _open_capture(self) -> bool:
        """Open video capture with retry logic."""
        for attempt in range(self.reconnect_attempts):
            cap = cv2.VideoCapture(self.source)
            if cap.isOpened():
                # Optimise buffer
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
                if isinstance(self.source, str) and self.source.startswith("rtsp"):
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"H264"))
                self._cap = cap
                logger.info(f"[StreamHandler] Stream opened: {self.source}")
                return True
            logger.warning(
                f"[StreamHandler] Attempt {attempt+1}/{self.reconnect_attempts} failed."
            )
            time.sleep(self.reconnect_delay)
        return False

    def _capture_loop(self):
        """Background thread: reads frames and enqueues them."""
        if not self._open_capture():
            logger.error("[StreamHandler] Failed to open stream. Exiting capture loop.")
            self._running = False
            return

        last_time = time.time()
        frame_count = 0
        skip_factor = 1  # Frame-skip optimisation

        while self._running:
            ret, frame = self._cap.read()
            if not ret:
                logger.warning("[StreamHandler] Frame read failed. Attempting reconnect…")
                self._cap.release()
                if not self._open_capture():
                    self._running = False
                    break
                continue

            frame_count += 1
            # Frame skipping for edge optimization
            if frame_count % skip_factor != 0:
                continue

            now = time.time()
            elapsed = now - last_time
            if elapsed < self._frame_interval:
                time.sleep(self._frame_interval - elapsed)
            last_time = time.time()

            # Resize for performance
            if self.resize:
                frame = cv2.resize(frame, self.resize)

            try:
                self.frame_queue.put_nowait((time.time(), frame))
            except Full:
                # Drop oldest frame to maintain real-time processing
                try:
                    self.frame_queue.get_nowait()
                    self.frame_queue.put_nowait((time.time(), frame))
                except Exception:
                    pass

        if self._cap:
            self._cap.release()
        logger.info("[StreamHandler] Capture loop terminated.")

    def start(self):
        """Start background capture thread."""
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("[StreamHandler] Stream handler started.")

    def stop(self):
        """Stop capture thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[StreamHandler] Stream handler stopped.")

    def read(self) -> Optional[Tuple[float, any]]:
        """Read next frame from queue. Returns (timestamp, frame) or None."""
        try:
            return self.frame_queue.get(timeout=2.0)
        except Exception:
            return None

    def frames(self) -> Generator:
        """Generator yielding (timestamp, frame) tuples."""
        while self._running:
            item = self.read()
            if item is not None:
                yield item

    @property
    def is_running(self) -> bool:
        return self._running

    def get_metadata(self) -> dict:
        """Return stream metadata."""
        if not self._cap:
            return {}
        return {
            "source": str(self.source),
            "fps": self._cap.get(cv2.CAP_PROP_FPS),
            "width": int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "target_fps": self.target_fps,
        }


class MultiStreamManager:
    """Manages multiple camera streams concurrently."""

    def __init__(self):
        self._streams: dict[str, StreamHandler] = {}

    def add_stream(self, camera_id: str, source, **kwargs) -> StreamHandler:
        handler = StreamHandler(source, **kwargs)
        handler.start()
        self._streams[camera_id] = handler
        logger.info(f"[MultiStreamManager] Added camera: {camera_id}")
        return handler

    def remove_stream(self, camera_id: str):
        if camera_id in self._streams:
            self._streams[camera_id].stop()
            del self._streams[camera_id]

    def get_stream(self, camera_id: str) -> Optional[StreamHandler]:
        return self._streams.get(camera_id)

    def stop_all(self):
        for handler in self._streams.values():
            handler.stop()
        self._streams.clear()
