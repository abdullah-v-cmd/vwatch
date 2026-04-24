"""
V-Watch Edge AI - Wrong Direction & Lane Violation Detector
Uses motion vector analysis for direction violations
"""

import cv2
import numpy as np
import logging
import time
from typing import Dict, List, Tuple, Optional
from ..trackers.deepsort_tracker import Track

logger = logging.getLogger(__name__)


class LaneDefinition:
    """Defines a lane and its permitted travel direction."""

    def __init__(
        self,
        lane_id: str,
        polygon: List[Tuple[int, int]],
        allowed_direction: Tuple[float, float],
        direction_tolerance: float = 60.0,
    ):
        self.lane_id = lane_id
        self.polygon = np.array(polygon, dtype=np.int32)
        self.allowed_direction = np.array(allowed_direction)
        self.direction_tolerance = direction_tolerance  # degrees

    def contains_point(self, point: Tuple[int, int]) -> bool:
        """Check if point is inside this lane polygon."""
        return cv2.pointPolygonTest(self.polygon, point, False) >= 0

    def is_wrong_direction(self, direction_vector: Tuple[float, float]) -> bool:
        """Check if movement direction violates lane rules."""
        if direction_vector == (0.0, 0.0):
            return False
        dv = np.array(direction_vector)
        ad = self.allowed_direction / (np.linalg.norm(self.allowed_direction) + 1e-6)
        dot = np.clip(np.dot(dv, ad), -1.0, 1.0)
        angle = np.degrees(np.arccos(dot))
        return angle > self.direction_tolerance

    def draw(self, frame: np.ndarray, color=(255, 165, 0), alpha=0.2) -> np.ndarray:
        overlay = frame.copy()
        cv2.fillPoly(overlay, [self.polygon], color)
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
        cv2.polylines(frame, [self.polygon], True, color, 2)
        # Draw allowed direction arrow
        M = cv2.moments(self.polygon)
        if M["m00"] != 0:
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])
            dx, dy = self.allowed_direction
            mag = (dx**2 + dy**2) ** 0.5 + 1e-6
            scale = 40
            end = (int(cx + dx / mag * scale), int(cy + dy / mag * scale))
            cv2.arrowedLine(frame, (cx, cy), end, (0, 255, 255), 2, tipLength=0.3)
        return frame


class WrongDirectionDetector:
    """
    Detects wrong-way driving using motion vector analysis.
    Compares vehicle trajectory against defined allowed directions per lane.
    """

    def __init__(
        self,
        lanes: List[LaneDefinition],
        min_frames_for_detection: int = 10,
        cooldown_seconds: float = 10.0,
    ):
        self.lanes = lanes
        self.min_frames = min_frames_for_detection
        self.cooldown = cooldown_seconds
        self._violation_cooldown: Dict[int, float] = {}

    def check_violation(self, track: Track) -> Tuple[bool, Optional[str]]:
        """
        Check if track is traveling in wrong direction.
        Returns (is_violation, lane_id or None).
        """
        if len(track.center_history) < self.min_frames:
            return False, None

        tid = track.track_id
        last_violation = self._violation_cooldown.get(tid, 0)
        if time.time() - last_violation < self.cooldown:
            return False, None

        direction = track.direction_vector
        if direction is None or direction == (0.0, 0.0):
            return False, None

        curr_pos = track.center
        for lane in self.lanes:
            if lane.contains_point(curr_pos):
                if lane.is_wrong_direction(direction):
                    self._violation_cooldown[tid] = time.time()
                    logger.warning(
                        f"[WrongDirection] Track {tid} wrong way in lane {lane.lane_id}!"
                    )
                    return True, lane.lane_id

        return False, None

    def draw_overlay(self, frame: np.ndarray, active_tracks: List[Track]) -> np.ndarray:
        for lane in self.lanes:
            frame = lane.draw(frame)
        return frame


class LaneViolationDetector:
    """
    Detects lane boundary violations - vehicles crossing lane markings.
    """

    def __init__(
        self,
        lane_boundaries: List[Tuple[Tuple[int, int], Tuple[int, int]]],
        cooldown_seconds: float = 5.0,
    ):
        """
        Args:
            lane_boundaries: List of line segments [(pt1, pt2), ...] representing lane markings
        """
        self.boundaries = lane_boundaries
        self.cooldown = cooldown_seconds
        self._track_side: Dict[int, int] = {}  # track_id -> side of line (-1, 0, 1)
        self._violation_cooldown: Dict[int, float] = {}

    def _side_of_line(self, line_start, line_end, point) -> int:
        """Determine which side of a line a point is on."""
        x1, y1 = line_start
        x2, y2 = line_end
        px, py = point
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        if cross > 0:
            return 1
        elif cross < 0:
            return -1
        return 0

    def check_violation(self, track: Track) -> Tuple[bool, Optional[int]]:
        """
        Check if vehicle has crossed a lane boundary.
        Returns (is_violation, boundary_index or None).
        """
        tid = track.track_id
        last_violation = self._violation_cooldown.get(tid, 0)
        if time.time() - last_violation < self.cooldown:
            return False, None

        curr_pos = track.center

        for i, (pt1, pt2) in enumerate(self.boundaries):
            curr_side = self._side_of_line(pt1, pt2, curr_pos)
            key = f"{tid}_{i}"
            prev_side = self._track_side.get(key)

            if prev_side is not None and prev_side != 0 and curr_side != 0:
                if prev_side != curr_side:
                    self._violation_cooldown[tid] = time.time()
                    logger.warning(
                        f"[LaneViolation] Track {tid} crossed lane boundary {i}!"
                    )
                    self._track_side[key] = curr_side
                    return True, i

            self._track_side[key] = curr_side

        return False, None

    def draw_boundaries(self, frame: np.ndarray) -> np.ndarray:
        for pt1, pt2 in self.boundaries:
            cv2.line(frame, pt1, pt2, (255, 255, 255), 2, cv2.LINE_AA)
        return frame
