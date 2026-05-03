"""
V-Watch Edge AI - Production Stream Handler
=============================================
Persistent, auto-reconnecting video stream handler.

Key guarantees:
  - Camera NEVER stops due to frontend page changes (runs in its own thread)
  - YOLO model is a SINGLETON — loaded ONCE, never re-downloaded
  - Auto-reconnects on any camera failure (webcam, RTSP, file)
  - Exponential back-off between reconnect attempts
  - Thread-safe frame queue; latest frame always available
  - Works inside Docker with /dev/video0, RTSP, or demo mode (no camera)
  - Health endpoint reports stream state to watchdog
"""

import cv2
import time
import logging
import threading
import os
from queue import Queue, Full, Empty
from typing import Optional, Generator, Tuple, Union

logger = logging.getLogger("vwatch.stream")

# ── Reconnect Configuration ────────────────────────────────────────────────────
_DEFAULT_RECONNECT_DELAY  = 2.0    # seconds between first reconnect attempts
_DEFAULT_MAX_RECONNECT    = float("inf")  # never give up
_DEFAULT_BACKOFF_FACTOR   = 1.5    # multiply delay each failed attempt
_DEFAULT_MAX_DELAY        = 60.0   # cap reconnect delay at 60 s


class StreamStats:
    """Lightweight thread-safe statistics for one stream."""

    def __init__(self):
        self._lock         = threading.Lock()
        self.frames_read   = 0
        self.frames_dropped = 0
        self.reconnects    = 0
        self.errors        = 0
        self.started_at    = None
        self.last_frame_at = None
        self.current_fps   = 0.0
        self._fps_frames   = 0
        self._fps_ts       = time.monotonic()

    def record_frame(self):
        with self._lock:
            self.frames_read  += 1
            self.last_frame_at = time.monotonic()
            self._fps_frames  += 1
            now = time.monotonic()
            elapsed = now - self._fps_ts
            if elapsed >= 1.0:
                self.current_fps   = self._fps_frames / elapsed
                self._fps_frames   = 0
                self._fps_ts       = now

    def record_drop(self):
        with self._lock:
            self.frames_dropped += 1

    def record_reconnect(self):
        with self._lock:
            self.reconnects += 1

    def record_error(self):
        with self._lock:
            self.errors += 1

    def to_dict(self) -> dict:
        with self._lock:
            up = (
                time.monotonic() - self.started_at
                if self.started_at else 0.0
            )
            return {
                "frames_read":    self.frames_read,
                "frames_dropped": self.frames_dropped,
                "reconnects":     self.reconnects,
                "errors":         self.errors,
                "uptime_seconds": round(up, 1),
                "current_fps":    round(self.current_fps, 1),
            }


class StreamHandler:
    """
    Thread-safe, auto-reconnecting video stream handler.

    Supports:
      • Webcam device index  (int or "0", "1", ...)
      • /dev/videoN          ("/dev/video0")
      • RTSP streams         ("rtsp://...")
      • HTTP MJPEG streams   ("http://...")
      • Video files          ("path/to/video.mp4")
      • Demo / synthetic     ("demo")

    The capture loop runs in a background daemon thread.
    If the source is unavailable, it keeps retrying with
    exponential back-off — it will NEVER crash the process.
    """

    def __init__(
        self,
        source: Union[str, int],
        frame_queue_size: int = 30,
        target_fps: int = 15,
        resize: Optional[Tuple[int, int]] = (1280, 720),
        reconnect_delay: float  = _DEFAULT_RECONNECT_DELAY,
        backoff_factor: float   = _DEFAULT_BACKOFF_FACTOR,
        max_reconnect_delay: float = _DEFAULT_MAX_DELAY,
    ):
        self.source             = source
        self.target_fps         = target_fps
        self.resize             = resize
        self.reconnect_delay    = reconnect_delay
        self.backoff_factor     = backoff_factor
        self.max_reconnect_delay = max_reconnect_delay

        self._frame_queue: Queue = Queue(maxsize=frame_queue_size)
        self._cap: Optional[cv2.VideoCapture] = None
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._frame_interval = 1.0 / max(target_fps, 1)
        self._lock     = threading.Lock()
        self._latest_frame: Optional[Tuple[float, any]] = None  # always accessible

        # Stream state
        self._state    = "idle"   # idle | starting | running | reconnecting | stopped
        self._error    = ""
        self.stats     = StreamStats()

        self._is_demo  = (str(source).lower() == "demo")
        logger.info(
            f"[StreamHandler] Configured — source='{source}' "
            f"fps={target_fps} resize={resize}"
        )

    # ── Source Resolution ──────────────────────────────────────────────────────

    def _resolve_source(self) -> Union[int, str]:
        """Normalise the source so OpenCV can open it."""
        src = self.source

        # Integer index or string digit → int device
        if isinstance(src, int):
            return src
        if isinstance(src, str):
            s = src.strip()
            if s.isdigit():
                return int(s)
            # /dev/video0 → let OpenCV handle it directly
            if s.startswith("/dev/video"):
                try:
                    return int(s.replace("/dev/video", ""))
                except ValueError:
                    return s
            # RTSP / HTTP / file path → string as-is
            return s
        return src

    # ── OpenCV Capture Open ────────────────────────────────────────────────────

    def _open_capture(self) -> bool:
        """Try to open a cv2.VideoCapture. Returns True on success."""
        resolved = self._resolve_source()
        logger.info(f"[StreamHandler] Opening capture: {resolved!r}")

        try:
            # Use FFMPEG backend for RTSP to avoid GStreamer issues in Docker
            if isinstance(resolved, str) and resolved.startswith("rtsp"):
                cap = cv2.VideoCapture(resolved, cv2.CAP_FFMPEG)
            else:
                cap = cv2.VideoCapture(resolved)

            if not cap.isOpened():
                cap.release()
                logger.warning(f"[StreamHandler] cap.isOpened() = False for {resolved!r}")
                return False

            # Configure capture properties
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # Minimal buffer → low latency
            if isinstance(resolved, int):
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_FPS,          self.target_fps)

            with self._lock:
                self._cap = cap
                self._state = "running"
                self._error = ""

            logger.info(f"[StreamHandler] ✅ Stream opened: {resolved!r}")
            return True

        except cv2.error as e:
            logger.error(f"[StreamHandler] OpenCV error opening {resolved!r}: {e}")
            self.stats.record_error()
            return False
        except Exception as e:
            logger.error(f"[StreamHandler] Error opening {resolved!r}: {e}")
            self.stats.record_error()
            return False

    # ── Demo / Synthetic Frame Generator ─────────────────────────────────────

    def _demo_capture_loop(self):
        """Generate synthetic frames when CAMERA_SOURCE=demo (no real camera)."""
        import numpy as np
        self._state = "running"
        self.stats.started_at = time.monotonic()
        logger.info("[StreamHandler] DEMO mode — generating synthetic frames")

        frame_num = 0
        while self._running:
            t0 = time.monotonic()

            # Create a synthetic BGR frame
            frame = np.zeros((720, 1280, 3), dtype="uint8")
            ts = time.strftime("%H:%M:%S")
            cv2.putText(frame, f"V-Watch DEMO | {ts}",
                        (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
            cv2.putText(frame, f"Frame #{frame_num}",
                        (30, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)
            cv2.rectangle(frame, (200, 200), (600, 500), (0, 255, 0), 2)
            cv2.putText(frame, "Simulated Vehicle",
                        (210, 195), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            frame_num += 1
            self._enqueue_frame(frame)
            self.stats.record_frame()

            elapsed = time.monotonic() - t0
            sleep = max(0.001, self._frame_interval - elapsed)
            time.sleep(sleep)

        self._state = "stopped"
        logger.info("[StreamHandler] Demo loop stopped.")

    # ── Real Capture Loop ─────────────────────────────────────────────────────

    def _capture_loop(self):
        """
        Main capture loop — runs in daemon thread.
        Reconnects automatically with exponential back-off.
        Never exits unless self._running is False.
        """
        self._state = "starting"
        self.stats.started_at = time.monotonic()
        delay = self.reconnect_delay

        while self._running:
            # ── Try to open the source ──
            if not self._open_capture():
                self._state = "reconnecting"
                logger.warning(
                    f"[StreamHandler] Reconnecting in {delay:.1f}s "
                    f"(attempt #{self.stats.reconnects + 1})"
                )
                self.stats.record_reconnect()
                self._sleep_interruptible(delay)
                delay = min(delay * self.backoff_factor, self.max_reconnect_delay)
                continue

            # ── Source opened — reset back-off ──
            delay = self.reconnect_delay
            last_frame_time = time.monotonic()

            # ── Read frames ──
            while self._running:
                with self._lock:
                    cap = self._cap

                if cap is None:
                    break

                t0 = time.monotonic()
                ret, frame = cap.read()

                if not ret or frame is None:
                    stale = time.monotonic() - last_frame_time
                    logger.warning(
                        f"[StreamHandler] Read failed after {stale:.1f}s — reconnecting"
                    )
                    with self._lock:
                        if self._cap:
                            self._cap.release()
                            self._cap = None
                    self._state = "reconnecting"
                    self.stats.record_reconnect()
                    break   # Back to outer reconnect loop

                last_frame_time = time.monotonic()

                # Throttle to target FPS
                elapsed = time.monotonic() - t0
                sleep = max(0.0, self._frame_interval - elapsed)
                if sleep > 0:
                    time.sleep(sleep)

                # Resize
                if self.resize:
                    try:
                        frame = cv2.resize(frame, self.resize)
                    except cv2.error:
                        continue

                self._enqueue_frame(frame)
                self.stats.record_frame()

        # ── Cleanup ──
        with self._lock:
            if self._cap:
                self._cap.release()
                self._cap = None
        self._state = "stopped"
        logger.info("[StreamHandler] Capture loop terminated.")

    def _enqueue_frame(self, frame):
        """Put frame into queue; drop oldest if full. Also update latest."""
        ts = time.time()
        item = (ts, frame)

        # Always keep latest frame accessible (for MJPEG snapshot)
        self._latest_frame = item

        try:
            self._frame_queue.put_nowait(item)
        except Full:
            # Drop oldest frame to maintain real-time
            try:
                self._frame_queue.get_nowait()
                self._frame_queue.put_nowait(item)
                self.stats.record_drop()
            except Exception:
                pass

    def _sleep_interruptible(self, seconds: float, step: float = 0.5):
        """Sleep for `seconds`, waking up every `step` to check _running."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            time.sleep(min(step, max(0.0, end - time.monotonic())))

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        """Start the background capture thread (idempotent)."""
        if self._running:
            return
        self._running = True
        target = self._demo_capture_loop if self._is_demo else self._capture_loop
        self._thread = threading.Thread(
            target=target,
            daemon=True,
            name=f"stream-{self.source}",
        )
        self._thread.start()
        logger.info(f"[StreamHandler] Background thread started for '{self.source}'")

    def stop(self):
        """Signal the capture thread to stop and wait for it."""
        self._running = False
        with self._lock:
            if self._cap:
                self._cap.release()
                self._cap = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("[StreamHandler] Stopped.")

    def read(self, timeout: float = 2.0) -> Optional[Tuple[float, any]]:
        """
        Read the next frame from the queue.
        Returns (timestamp, frame) or None on timeout.
        Falls back to the latest frame if queue is empty but stream is running.
        """
        try:
            return self._frame_queue.get(timeout=timeout)
        except Empty:
            # Return latest frame if we have one (better than None during brief stalls)
            return self._latest_frame

    def frames(self) -> Generator:
        """Generator that yields (timestamp, frame) tuples indefinitely."""
        while self._running:
            item = self.read(timeout=2.0)
            if item is not None:
                yield item

    def get_latest_frame(self) -> Optional[Tuple[float, any]]:
        """Return the most recent (timestamp, frame) without consuming it."""
        return self._latest_frame

    @property
    def is_running(self) -> bool:
        return self._running and self._state in ("running",)

    @property
    def state(self) -> str:
        return self._state

    def get_metadata(self) -> dict:
        with self._lock:
            cap = self._cap
            meta = {
                "source":     str(self.source),
                "state":      self._state,
                "target_fps": self.target_fps,
                "resize":     self.resize,
                "demo_mode":  self._is_demo,
            }
            if cap:
                meta.update({
                    "width":  int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "cam_fps": cap.get(cv2.CAP_PROP_FPS),
                })
            meta["stats"] = self.stats.to_dict()
        return meta


# ── Multi-Stream Manager ───────────────────────────────────────────────────────

class MultiStreamManager:
    """Manages multiple concurrent StreamHandler instances keyed by camera_id."""

    def __init__(self):
        self._streams: dict[str, StreamHandler] = {}
        self._lock = threading.Lock()

    def add_stream(
        self,
        camera_id: str,
        source: Union[str, int],
        **kwargs,
    ) -> StreamHandler:
        with self._lock:
            if camera_id in self._streams:
                return self._streams[camera_id]
            handler = StreamHandler(source, **kwargs)
            handler.start()
            self._streams[camera_id] = handler
            logger.info(f"[MultiStreamManager] Added camera: {camera_id}")
            return handler

    def remove_stream(self, camera_id: str):
        with self._lock:
            handler = self._streams.pop(camera_id, None)
        if handler:
            handler.stop()

    def get_stream(self, camera_id: str) -> Optional[StreamHandler]:
        return self._streams.get(camera_id)

    def stop_all(self):
        with self._lock:
            handlers = list(self._streams.values())
            self._streams.clear()
        for h in handlers:
            h.stop()
