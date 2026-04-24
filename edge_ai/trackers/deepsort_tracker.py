"""
V-Watch Edge AI - DeepSORT Multi-Object Tracker
Assigns persistent IDs to detected vehicles across frames
"""

import numpy as np
import logging
import time
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


@dataclass
class TrackState:
    TENTATIVE = 1
    CONFIRMED = 2
    DELETED = 3


@dataclass
class Track:
    """Represents a tracked vehicle."""
    track_id: int
    class_name: str
    bbox: Tuple[int, int, int, int]
    confidence: float
    state: int = TrackState.CONFIRMED
    hits: int = 1
    age: int = 0
    time_since_update: int = 0
    center_history: deque = field(default_factory=lambda: deque(maxlen=60))
    speed_history: deque = field(default_factory=lambda: deque(maxlen=10))
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    def __post_init__(self):
        x1, y1, x2, y2 = self.bbox
        self.center_history.append(((x1 + x2) // 2, (y1 + y2) // 2))

    @property
    def center(self):
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) // 2, (y1 + y2) // 2)

    def update(self, bbox, confidence, class_name=None):
        self.bbox = bbox
        self.confidence = confidence
        if class_name:
            self.class_name = class_name
        self.hits += 1
        self.age += 1
        self.time_since_update = 0
        self.last_seen = time.time()
        cx, cy = self.center
        self.center_history.append((cx, cy))

    def mark_missed(self):
        self.time_since_update += 1
        self.age += 1

    @property
    def is_confirmed(self):
        return self.state == TrackState.CONFIRMED

    @property
    def direction_vector(self) -> Optional[Tuple[float, float]]:
        """Compute movement direction from history."""
        if len(self.center_history) < 5:
            return None
        pts = list(self.center_history)
        dx = pts[-1][0] - pts[0][0]
        dy = pts[-1][1] - pts[0][1]
        magnitude = (dx**2 + dy**2) ** 0.5
        if magnitude < 1e-3:
            return (0.0, 0.0)
        return (dx / magnitude, dy / magnitude)


class IoUMatcher:
    """Simple IoU-based matcher for track-detection association."""

    @staticmethod
    def iou(bbox1, bbox2) -> float:
        x1 = max(bbox1[0], bbox2[0])
        y1 = max(bbox1[1], bbox2[1])
        x2 = min(bbox1[2], bbox2[2])
        y2 = min(bbox1[3], bbox2[3])
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
        area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
        union = area1 + area2 - inter
        return inter / union if union > 0 else 0.0

    @classmethod
    def match(
        cls,
        tracks: List[Track],
        detections: List,
        iou_threshold: float = 0.3,
    ) -> Tuple[List, List, List]:
        """
        Match tracks to detections using IoU.
        Returns: (matched_pairs, unmatched_tracks, unmatched_detections)
        """
        if not tracks or not detections:
            return [], list(range(len(tracks))), list(range(len(detections)))

        iou_matrix = np.zeros((len(tracks), len(detections)))
        for t, track in enumerate(tracks):
            for d, det in enumerate(detections):
                iou_matrix[t, d] = cls.iou(track.bbox, det.bbox)

        matched_pairs = []
        unmatched_tracks = list(range(len(tracks)))
        unmatched_dets = list(range(len(detections)))

        # Greedy matching
        while True:
            if iou_matrix.size == 0:
                break
            max_iou = iou_matrix.max()
            if max_iou < iou_threshold:
                break
            t_idx, d_idx = np.unravel_index(iou_matrix.argmax(), iou_matrix.shape)
            matched_pairs.append((t_idx, d_idx))
            iou_matrix[t_idx, :] = -1
            iou_matrix[:, d_idx] = -1
            unmatched_tracks.remove(t_idx)
            unmatched_dets.remove(d_idx)

        return matched_pairs, unmatched_tracks, unmatched_dets


class DeepSORTTracker:
    """
    Production DeepSORT-inspired multi-object tracker.
    Falls back to IoU-based tracking when deep-sort-realtime is unavailable.
    """

    def __init__(
        self,
        max_age: int = 30,
        min_hits: int = 3,
        iou_threshold: float = 0.3,
        use_deep_features: bool = True,
    ):
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.use_deep_features = use_deep_features
        self._next_id = 1
        self._tracks: Dict[int, Track] = {}
        self._deep_tracker = None
        self._init_deep_tracker()

    def _init_deep_tracker(self):
        """Try to initialise deep-sort-realtime tracker."""
        if not self.use_deep_features:
            return
        try:
            from deep_sort_realtime.deepsort_tracker import DeepSort
            self._deep_tracker = DeepSort(
                max_age=self.max_age,
                n_init=self.min_hits,
                nms_max_overlap=1.0,
                max_cosine_distance=0.3,
            )
            logger.info("[DeepSORTTracker] deep-sort-realtime initialized.")
        except ImportError:
            logger.warning("[DeepSORTTracker] deep-sort-realtime not found. Using IoU tracker.")
            self._deep_tracker = None

    def update(self, detections: List, frame: np.ndarray = None) -> List[Track]:
        """
        Update tracker with new detections.
        Returns list of confirmed Track objects.
        """
        if self._deep_tracker and frame is not None:
            return self._update_deep(detections, frame)
        return self._update_iou(detections)

    def _update_deep(self, detections: List, frame: np.ndarray) -> List[Track]:
        """Update using deep-sort-realtime."""
        try:
            raw_dets = []
            for det in detections:
                x1, y1, x2, y2 = det.bbox
                raw_dets.append(([x1, y1, x2 - x1, y2 - y1], det.confidence, det.class_name))

            tracks = self._deep_tracker.update_tracks(raw_dets, frame=frame)
            result = []
            for t in tracks:
                if not t.is_confirmed():
                    continue
                tid = t.track_id
                ltrb = t.to_ltrb()
                x1, y1, x2, y2 = map(int, ltrb)
                if tid not in self._tracks:
                    self._tracks[tid] = Track(
                        track_id=tid,
                        class_name=t.det_class or "vehicle",
                        bbox=(x1, y1, x2, y2),
                        confidence=t.det_conf or 0.8,
                    )
                else:
                    self._tracks[tid].update((x1, y1, x2, y2), t.det_conf or 0.8, t.det_class)
                result.append(self._tracks[tid])
            return result
        except Exception as e:
            logger.error(f"[DeepSORTTracker] Deep update error: {e}")
            return self._update_iou(detections)

    def _update_iou(self, detections: List) -> List[Track]:
        """IoU-based fallback tracker."""
        # Mark all tracks as missed initially
        for track in self._tracks.values():
            track.mark_missed()

        active_tracks = [t for t in self._tracks.values() if t.time_since_update <= self.max_age]
        matched, unmatched_tracks_idx, unmatched_dets_idx = IoUMatcher.match(
            active_tracks, detections, self.iou_threshold
        )

        # Update matched tracks
        for t_idx, d_idx in matched:
            track = active_tracks[t_idx]
            det = detections[d_idx]
            track.update(det.bbox, det.confidence, det.class_name)
            track.time_since_update = 0

        # Create new tracks for unmatched detections
        for d_idx in unmatched_dets_idx:
            det = detections[d_idx]
            new_track = Track(
                track_id=self._next_id,
                class_name=det.class_name,
                bbox=det.bbox,
                confidence=det.confidence,
                hits=1,
                state=TrackState.TENTATIVE,
            )
            self._tracks[self._next_id] = new_track
            self._next_id += 1

        # Confirm tentative tracks with enough hits
        for track in self._tracks.values():
            if track.state == TrackState.TENTATIVE and track.hits >= self.min_hits:
                track.state = TrackState.CONFIRMED

        # Remove stale tracks
        stale_ids = [
            tid for tid, t in self._tracks.items()
            if t.time_since_update > self.max_age
        ]
        for tid in stale_ids:
            del self._tracks[tid]

        return [t for t in self._tracks.values() if t.is_confirmed]

    def get_track(self, track_id: int) -> Optional[Track]:
        return self._tracks.get(track_id)

    def get_all_tracks(self) -> List[Track]:
        return [t for t in self._tracks.values() if t.is_confirmed]
