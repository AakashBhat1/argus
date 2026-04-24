"""Trajectory accumulation for tracked objects.

Buffers per-object positions frame-by-frame and computes trajectory
features when a track is dropped by DeepSORT (object leaves scene).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Minimum frames to consider a trajectory valid for classification.
MIN_TRAJECTORY_LENGTH = 5

# Speed below this (pixels/sec) is considered stationary.
STATIONARY_SPEED_THRESHOLD = 2.0

# Minimum angle change (degrees) to count as a direction change.
DIRECTION_CHANGE_ANGLE_THRESHOLD = 45.0


@dataclass
class TrajectoryPoint:
    x: float
    y: float
    w: float
    h: float
    confidence: float
    frame_number: int
    timestamp: float  # monotonic seconds
    roi_zone_ids: list[int] = field(default_factory=list)
    has_intrusion: bool = False


@dataclass
class TrajectoryFeatures:
    """Computed features from a complete trajectory."""
    object_id: int
    class_label: str
    duration_sec: float
    total_distance: float
    avg_speed: float
    max_speed: float
    direction_changes: int
    stationary_ratio: float
    bbox_coverage: float
    entry_point: list[float]
    exit_point: list[float]
    roi_zones_visited: list[int]
    had_intrusion: bool
    trajectory_points: list[list[float]]  # [[x, y], ...] sampled
    point_count: int

    def to_feature_dict(self) -> dict:
        return {
            "duration_sec": round(self.duration_sec, 2),
            "total_distance": round(self.total_distance, 1),
            "avg_speed": round(self.avg_speed, 2),
            "max_speed": round(self.max_speed, 2),
            "direction_changes": self.direction_changes,
            "stationary_ratio": round(self.stationary_ratio, 3),
            "bbox_coverage": round(self.bbox_coverage, 4),
            "had_intrusion": self.had_intrusion,
            "roi_zone_count": len(self.roi_zones_visited),
            "point_count": self.point_count,
        }


class TrajectoryAccumulator:
    """Buffers trajectory points per object_id and emits features on track end."""

    def __init__(self, max_age_seconds: float = 60.0):
        self._buffers: dict[int, list[TrajectoryPoint]] = {}
        self._class_labels: dict[int, str] = {}
        self._last_seen: dict[int, float] = {}
        self._max_age = max_age_seconds

    def update(self, tracked_objects: list[dict]) -> list[TrajectoryFeatures]:
        """Add current frame's tracked objects and return features for any ended tracks.

        A track is considered ended when its object_id was present in previous
        frames but is absent in the current frame (DeepSORT dropped it).
        """
        now = time.monotonic()
        current_ids = set()

        for obj in tracked_objects:
            oid = int(obj.get("object_id", -1))
            if oid < 0:
                continue
            current_ids.add(oid)
            self._last_seen[oid] = now
            self._class_labels[oid] = str(obj.get("class_label", "unknown"))

            point = TrajectoryPoint(
                x=float(obj.get("bbox_x", 0)) + float(obj.get("bbox_w", 0)) / 2,
                y=float(obj.get("bbox_y", 0)) + float(obj.get("bbox_h", 0)) / 2,
                w=float(obj.get("bbox_w", 0)),
                h=float(obj.get("bbox_h", 0)),
                confidence=float(obj.get("confidence", 0)),
                frame_number=int(obj.get("frame_number", 0)),
                timestamp=now,
                roi_zone_ids=[int(z) for z in obj.get("roi_zone_ids", [])],
                has_intrusion=bool(obj.get("intrusion", False)),
            )
            self._buffers.setdefault(oid, []).append(point)

        # Check for ended tracks
        ended_features: list[TrajectoryFeatures] = []
        ended_ids = []

        for oid in list(self._buffers.keys()):
            if oid in current_ids:
                continue
            last = self._last_seen.get(oid, 0)
            # Give 2 seconds grace period before considering track ended
            if (now - last) < 2.0:
                continue
            ended_ids.append(oid)

        for oid in ended_ids:
            points = self._buffers.pop(oid, [])
            class_label = self._class_labels.pop(oid, "unknown")
            self._last_seen.pop(oid, None)

            if len(points) < MIN_TRAJECTORY_LENGTH:
                continue

            features = _compute_features(oid, class_label, points)
            if features is not None:
                ended_features.append(features)

        # Cleanup stale buffers (safety net)
        stale_ids = [
            oid for oid, last in self._last_seen.items()
            if (now - last) > self._max_age and oid not in current_ids
        ]
        for oid in stale_ids:
            points = self._buffers.pop(oid, [])
            class_label = self._class_labels.pop(oid, "unknown")
            self._last_seen.pop(oid, None)
            if len(points) >= MIN_TRAJECTORY_LENGTH:
                features = _compute_features(oid, class_label, points)
                if features is not None:
                    ended_features.append(features)

        return ended_features

    def flush_all(self) -> list[TrajectoryFeatures]:
        """Force-end all active tracks and return their features."""
        results: list[TrajectoryFeatures] = []
        for oid in list(self._buffers.keys()):
            points = self._buffers.pop(oid, [])
            class_label = self._class_labels.pop(oid, "unknown")
            self._last_seen.pop(oid, None)
            if len(points) >= MIN_TRAJECTORY_LENGTH:
                features = _compute_features(oid, class_label, points)
                if features is not None:
                    results.append(features)
        return results

    def reset(self) -> None:
        self._buffers.clear()
        self._class_labels.clear()
        self._last_seen.clear()

    @property
    def active_track_count(self) -> int:
        return len(self._buffers)


def _compute_features(
    object_id: int,
    class_label: str,
    points: list[TrajectoryPoint],
) -> Optional[TrajectoryFeatures]:
    """Compute trajectory features from a sequence of points."""
    if len(points) < 2:
        return None

    duration = points[-1].timestamp - points[0].timestamp
    if duration <= 0:
        return None

    # Compute distances and speeds between consecutive points
    distances: list[float] = []
    speeds: list[float] = []
    for i in range(1, len(points)):
        dx = points[i].x - points[i - 1].x
        dy = points[i].y - points[i - 1].y
        dist = math.sqrt(dx * dx + dy * dy)
        distances.append(dist)
        dt = points[i].timestamp - points[i - 1].timestamp
        if dt > 0:
            speeds.append(dist / dt)
        else:
            speeds.append(0.0)

    total_distance = sum(distances)
    avg_speed = total_distance / duration if duration > 0 else 0.0
    max_speed = max(speeds) if speeds else 0.0

    # Direction changes
    direction_changes = _count_direction_changes(points)

    # Stationary ratio (fraction of time with near-zero speed)
    stationary_segments = sum(1 for s in speeds if s < STATIONARY_SPEED_THRESHOLD)
    stationary_ratio = stationary_segments / len(speeds) if speeds else 0.0

    # Bounding box coverage (fraction of frame area the trajectory spans)
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    trajectory_area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    # Estimate frame area from object size (rough heuristic)
    avg_w = sum(p.w for p in points) / len(points)
    avg_h = sum(p.h for p in points) / len(points)
    # Use a reference frame size estimate — assumes objects are ~5-15% of frame
    frame_area_est = max(trajectory_area, avg_w * avg_h * 100)
    bbox_coverage = trajectory_area / frame_area_est if frame_area_est > 0 else 0.0

    # ROI zones visited
    all_zones: set[int] = set()
    had_intrusion = False
    for p in points:
        all_zones.update(p.roi_zone_ids)
        if p.has_intrusion:
            had_intrusion = True

    # Sample trajectory for storage (max 50 points)
    step = max(1, len(points) // 50)
    sampled = [[round(p.x, 1), round(p.y, 1)] for p in points[::step]]

    return TrajectoryFeatures(
        object_id=object_id,
        class_label=class_label,
        duration_sec=duration,
        total_distance=total_distance,
        avg_speed=avg_speed,
        max_speed=max_speed,
        direction_changes=direction_changes,
        stationary_ratio=stationary_ratio,
        bbox_coverage=bbox_coverage,
        entry_point=[round(points[0].x, 1), round(points[0].y, 1)],
        exit_point=[round(points[-1].x, 1), round(points[-1].y, 1)],
        roi_zones_visited=sorted(all_zones),
        had_intrusion=had_intrusion,
        trajectory_points=sampled,
        point_count=len(points),
    )


def _count_direction_changes(points: list[TrajectoryPoint]) -> int:
    """Count significant direction changes in the trajectory."""
    if len(points) < 3:
        return 0

    changes = 0
    for i in range(2, len(points)):
        dx1 = points[i - 1].x - points[i - 2].x
        dy1 = points[i - 1].y - points[i - 2].y
        dx2 = points[i].x - points[i - 1].x
        dy2 = points[i].y - points[i - 1].y

        mag1 = math.sqrt(dx1 * dx1 + dy1 * dy1)
        mag2 = math.sqrt(dx2 * dx2 + dy2 * dy2)

        if mag1 < 0.5 or mag2 < 0.5:
            continue

        cos_angle = (dx1 * dx2 + dy1 * dy2) / (mag1 * mag2)
        cos_angle = max(-1.0, min(1.0, cos_angle))
        angle_deg = math.degrees(math.acos(cos_angle))

        if angle_deg >= DIRECTION_CHANGE_ANGLE_THRESHOLD:
            changes += 1

    return changes
