"""
V-Watch Edge AI - Red Light Violation Detector
Line-crossing detection with traffic signal state awareness
"""

import cv2
import numpy as np
import logging
import time
from typing import Dict, List, Tuple, Optional
from ..trackers.deepsort_tracker import Track

logger = logging.getLogger(__name__)


class SignalState:
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"
    UNKNOWN = "unknown"


class TrafficSignalDetector:
    """
    Detects traffic signal state using color segmentation.
    Can be replaced by a dedicated YOLO signal classifier.
    """

    def detect(self, frame: np.ndarray, roi: Tuple[int, int, int, int] = None) -> str:
        """
        Detect signal color in frame or ROI.
        Returns SignalState string.
        """
        if roi:
            x1, y1, x2, y2 = roi
            region = frame[y1:y2, x1:x2]
        else:
            region = frame

        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)

        # Red detection (wraps around in HSV)
        red_lower1 = np.array([0, 120, 70])
        red_upper1 = np.array([10, 255, 255])
        red_lower2 = np.array([170, 120, 70])
        red_upper2 = np.array([180, 255, 255])
        mask_red = cv2.bitwise_or(
            cv2.inRange(hsv, red_lower1, red_upper1),
            cv2.inRange(hsv, red_lower2, red_upper2),
        )

        # Green detection
        green_lower = np.array([40, 50, 50])
        green_upper = np.array([90, 255, 255])
        mask_green = cv2.inRange(hsv, green_lower, green_upper)

        # Yellow detection
        yellow_lower = np.array([15, 100, 100])
        yellow_upper = np.array([35, 255, 255])
        mask_yellow = cv2.inRange(hsv, yellow_lower, yellow_upper)

        counts = {
            SignalState.RED: cv2.countNonZero(mask_red),
            SignalState.GREEN: cv2.countNonZero(mask_green),
            SignalState.YELLOW: cv2.countNonZero(mask_yellow),
        }
        dominant = max(counts, key=counts.get)
        if counts[dominant] < 50:
            return SignalState.UNKNOWN
        return dominant


class StopLine:
    """Represents a virtual stop line in the scene."""

    def __init__(
        self,
        line_start: Tuple[int, int],
        line_end: Tuple[int, int],
        direction: str = "horizontal",
        margin: int = 10,
    ):
        self.start = line_start
        self.end = line_end
        self.direction = direction
        self.margin = margin

    def get_position(self) -> float:
        """Get the primary axis position of the stop line."""
        if self.direction == "horizontal":
            return (self.start[1] + self.end[1]) / 2  # Y position
        return (self.start[0] + self.end[0]) / 2      # X position

    def has_crossed(self, prev_center: Tuple[int, int], curr_center: Tuple[int, int]) -> bool:
        """Check if a vehicle has crossed this stop line."""
        line_pos = self.get_position()
        if self.direction == "horizontal":
            prev_val = prev_center[1]
            curr_val = curr_center[1]
        else:
            prev_val = prev_center[0]
            curr_val = curr_center[0]

        # Check if crossed the line (accounting for direction)
        return (prev_val < line_pos <= curr_val) or (prev_val > line_pos >= curr_val)

    def draw(self, frame: np.ndarray, signal_state: str = SignalState.UNKNOWN) -> np.ndarray:
        color_map = {
            SignalState.RED: (0, 0, 255),
            SignalState.GREEN: (0, 255, 0),
            SignalState.YELLOW: (0, 255, 255),
            SignalState.UNKNOWN: (200, 200, 200),
        }
        color = color_map.get(signal_state, (200, 200, 200))
        cv2.line(frame, self.start, self.end, color, 3)
        cv2.putText(frame, f"STOP LINE [{signal_state.upper()}]",
                    (self.start[0], self.start[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        return frame


class RedLightViolationDetector:
    """
    Detects red-light violations by correlating:
    - Vehicle line crossing events
    - Traffic signal state at crossing moment
    """

    def __init__(
        self,
        stop_lines: List[StopLine],
        signal_roi: Optional[Tuple[int, int, int, int]] = None,
        cooldown_seconds: float = 5.0,
    ):
        self.stop_lines = stop_lines
        self.signal_roi = signal_roi
        self.cooldown = cooldown_seconds
        self._signal_detector = TrafficSignalDetector()
        self._current_signal = SignalState.UNKNOWN
        self._track_prev_positions: Dict[int, Tuple[int, int]] = {}
        self._violation_cooldown: Dict[int, float] = {}  # track_id -> last_violation_time
        self._manual_signal_override: Optional[str] = None

    def set_signal_state(self, state: str):
        """Manually set signal state (from external sensor/API)."""
        self._manual_signal_override = state

    def update_signal(self, frame: np.ndarray):
        """Update signal state from frame analysis."""
        if self._manual_signal_override:
            self._current_signal = self._manual_signal_override
            return
        self._current_signal = self._signal_detector.detect(frame, self.signal_roi)

    def check_violation(
        self, track: Track, frame: np.ndarray
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if track has a red-light violation.
        Returns (is_violation, stop_line_id).
        """
        tid = track.track_id
        curr_center = track.center

        # Check cooldown
        last_violation = self._violation_cooldown.get(tid, 0)
        if time.time() - last_violation < self.cooldown:
            return False, None

        prev_center = self._track_prev_positions.get(tid)
        self._track_prev_positions[tid] = curr_center

        if prev_center is None:
            return False, None

        # Only flag if signal is RED
        if self._current_signal != SignalState.RED:
            return False, None

        for i, line in enumerate(self.stop_lines):
            if line.has_crossed(prev_center, curr_center):
                self._violation_cooldown[tid] = time.time()
                logger.warning(
                    f"[RedLight] Track {tid} crossed stop line {i} on RED signal!"
                )
                return True, f"stop_line_{i}"

        return False, None

    def draw_overlay(self, frame: np.ndarray) -> np.ndarray:
        """Draw stop lines and signal state on frame."""
        for line in self.stop_lines:
            frame = line.draw(frame, self._current_signal)
        # Signal indicator
        color_map = {
            SignalState.RED: (0, 0, 255),
            SignalState.GREEN: (0, 255, 0),
            SignalState.YELLOW: (0, 255, 255),
            SignalState.UNKNOWN: (150, 150, 150),
        }
        color = color_map.get(self._current_signal, (150, 150, 150))
        cv2.circle(frame, (50, 50), 25, color, -1)
        cv2.putText(frame, self._current_signal.upper(),
                    (85, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        return frame
