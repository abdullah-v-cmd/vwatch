"""
V-Watch Edge AI - Speed Violation Detector
Uses homography-based real-world distance estimation
"""

import cv2
import numpy as np
import time
import logging
from typing import Dict, List, Optional, Tuple
from ..trackers.deepsort_tracker import Track

logger = logging.getLogger(__name__)


class HomographySpeedEstimator:
    """
    Estimates real-world speed using homography transformation.
    Maps pixel coordinates to real-world metric coordinates.
    """

    def __init__(
        self,
        reference_points_image: List[Tuple[float, float]],
        reference_points_world: List[Tuple[float, float]],
        speed_limit_kmh: float = 60.0,
        measurement_interval_frames: int = 5,
    ):
        """
        Args:
            reference_points_image: 4+ pixel coords [(x,y), ...]
            reference_points_world: Corresponding real-world coords in meters [(x,y), ...]
            speed_limit_kmh: Speed threshold for violation
            measurement_interval_frames: Frames between measurements
        """
        self.speed_limit = speed_limit_kmh
        self.measurement_interval = measurement_interval_frames
        self._homography_matrix = self._compute_homography(
            reference_points_image, reference_points_world
        )
        self._track_data: Dict[int, dict] = {}  # track_id -> {positions, timestamps}

    def _compute_homography(self, img_pts, world_pts) -> Optional[np.ndarray]:
        """Compute homography from image to world coordinates."""
        if len(img_pts) < 4 or len(world_pts) < 4:
            logger.warning("[SpeedDetector] Not enough reference points for homography.")
            return None
        src = np.float32(img_pts)
        dst = np.float32(world_pts)
        H, _ = cv2.findHomography(src, dst)
        return H

    def image_to_world(self, point: Tuple[float, float]) -> Tuple[float, float]:
        """Transform image pixel to world coordinate (meters)."""
        if self._homography_matrix is None:
            return point
        px, py = point
        p = np.array([[[px, py]]], dtype=np.float32)
        world = cv2.perspectiveTransform(p, self._homography_matrix)
        return float(world[0][0][0]), float(world[0][0][1])

    def update(self, track: Track, timestamp: float) -> Optional[float]:
        """
        Update speed estimation for a track.
        Returns speed in km/h or None if not enough data.
        """
        tid = track.track_id
        cx, cy = track.center

        if tid not in self._track_data:
            self._track_data[tid] = {
                "positions": [],
                "timestamps": [],
                "frame_count": 0,
            }

        data = self._track_data[tid]
        data["frame_count"] += 1

        if data["frame_count"] % self.measurement_interval == 0:
            world_pos = self.image_to_world((cx, cy))
            data["positions"].append(world_pos)
            data["timestamps"].append(timestamp)

            # Keep only last 5 measurements
            if len(data["positions"]) > 5:
                data["positions"].pop(0)
                data["timestamps"].pop(0)

            if len(data["positions"]) >= 2:
                return self._calculate_speed(data)

        return None

    def _calculate_speed(self, data: dict) -> float:
        """Calculate speed from position history."""
        p1 = data["positions"][-2]
        p2 = data["positions"][-1]
        t1 = data["timestamps"][-2]
        t2 = data["timestamps"][-1]

        dt = t2 - t1
        if dt <= 0:
            return 0.0

        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        distance_m = (dx**2 + dy**2) ** 0.5
        speed_ms = distance_m / dt
        speed_kmh = speed_ms * 3.6
        return round(speed_kmh, 1)

    def is_speeding(self, track: Track, timestamp: float) -> Tuple[bool, float]:
        """
        Check if vehicle is speeding.
        Returns (is_violation, speed_kmh).
        """
        speed = self.update(track, timestamp)
        if speed is None:
            return False, 0.0
        # Update track speed history
        track.speed_history.append(speed)
        avg_speed = sum(track.speed_history) / len(track.speed_history)
        return avg_speed > self.speed_limit, avg_speed

    def cleanup_stale_tracks(self, active_ids: List[int]):
        """Remove data for tracks no longer active."""
        stale = [tid for tid in self._track_data if tid not in active_ids]
        for tid in stale:
            del self._track_data[tid]


class DefaultSpeedEstimator:
    """
    Simple pixel-based speed estimator when homography is unavailable.
    Uses pixel displacement and assumed camera parameters.
    """

    def __init__(self, speed_limit_kmh: float = 60.0, pixels_per_meter: float = 20.0, fps: float = 15.0):
        self.speed_limit = speed_limit_kmh
        self.pixels_per_meter = pixels_per_meter
        self.fps = fps
        self._prev_positions: Dict[int, Tuple] = {}
        self._prev_times: Dict[int, float] = {}

    def estimate_speed(self, track: Track, timestamp: float) -> float:
        tid = track.track_id
        cx, cy = track.center

        if tid in self._prev_positions:
            prev_cx, prev_cy = self._prev_positions[tid]
            dt = timestamp - self._prev_times.get(tid, timestamp)
            if dt > 0:
                pixel_dist = ((cx - prev_cx)**2 + (cy - prev_cy)**2) ** 0.5
                meters = pixel_dist / self.pixels_per_meter
                speed_ms = meters / dt
                speed_kmh = speed_ms * 3.6
                self._prev_positions[tid] = (cx, cy)
                self._prev_times[tid] = timestamp
                return round(speed_kmh, 1)

        self._prev_positions[tid] = (cx, cy)
        self._prev_times[tid] = timestamp
        return 0.0

    def is_speeding(self, track: Track, timestamp: float) -> Tuple[bool, float]:
        speed = self.estimate_speed(track, timestamp)
        return speed > self.speed_limit, speed
