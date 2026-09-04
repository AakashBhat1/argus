"""Monocular camera geometry: distance estimation and ground-plane mapping.

Ported from the drone interceptor ``CameraGeometry`` (pinhole + known object
size) and extended for fixed surveillance cameras:

* Per-class real-world size table (person height, car height, ...).
* Optional ground-plane homography calibrated from four image points with
  known real-world spacing (e.g. a parking bay or a measured rectangle).
  When present it yields metric ground coordinates for every foot point,
  which makes inter-object distances and speeds meaningful.

Only ``numpy``/``cv2`` are required; no drone/telemetry coupling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

import cv2
import numpy as np

# Real-world characteristic dimension used for the pinhole estimate, in metres.
# ``height`` is preferred for upright classes (person) because bbox width
# changes with pose; ``width``/``length`` would be the projected span for
# vehicles seen side-on, so height is used for those as well - it is the most
# viewpoint-stable dimension for a fixed CCTV camera.
DEFAULT_CLASS_SIZES_M: dict[str, dict[str, float]] = {
    "person": {"height": 1.70},
    "bicycle": {"height": 1.05},
    "motorcycle": {"height": 1.15},
    "car": {"height": 1.50},
    "bus": {"height": 3.20},
    "truck": {"height": 2.80},
    "dog": {"height": 0.55},
    "cat": {"height": 0.28},
}

MIN_RANGE_M = 0.5
MAX_RANGE_M = 500.0


@dataclass
class GroundPoint:
    x_m: float
    y_m: float

    def distance_to(self, other: "GroundPoint") -> float:
        return math.hypot(self.x_m - other.x_m, self.y_m - other.y_m)


@dataclass
class CameraCalibration:
    """Serializable calibration payload stored on ``Camera.calibration``."""

    hfov_deg: float = 84.0
    # Optional ground-plane homography inputs (normalized image coords 0..1).
    homography_image_points: list[list[float]] = field(default_factory=list)
    # Matching real-world points in metres (any planar frame, e.g. bay corners).
    homography_world_points: list[list[float]] = field(default_factory=list)
    class_sizes_m: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Any) -> "CameraCalibration":
        if not isinstance(payload, dict):
            return cls()
        hfov = payload.get("hfov_deg", 84.0)
        try:
            hfov = float(hfov)
        except (TypeError, ValueError):
            hfov = 84.0
        if not (10.0 <= hfov <= 170.0):
            hfov = 84.0
        img_pts = payload.get("homography_image_points") or []
        world_pts = payload.get("homography_world_points") or []
        sizes = payload.get("class_sizes_m") or {}
        return cls(
            hfov_deg=hfov,
            homography_image_points=[[float(p[0]), float(p[1])] for p in img_pts if len(p) >= 2],
            homography_world_points=[[float(p[0]), float(p[1])] for p in world_pts if len(p) >= 2],
            class_sizes_m={
                str(k).lower(): {str(dk): float(dv) for dk, dv in v.items()}
                for k, v in sizes.items()
                if isinstance(v, dict)
            },
        )

    def to_dict(self) -> dict:
        return {
            "hfov_deg": self.hfov_deg,
            "homography_image_points": self.homography_image_points,
            "homography_world_points": self.homography_world_points,
            "class_sizes_m": self.class_sizes_m,
        }

    @property
    def has_homography(self) -> bool:
        return (
            len(self.homography_image_points) >= 4
            and len(self.homography_image_points) == len(self.homography_world_points)
        )


class CameraGeometry:
    """Pinhole camera model with per-class size priors and optional ground homography."""

    def __init__(
        self,
        image_width: int = 1280,
        image_height: int = 720,
        calibration: Optional[CameraCalibration] = None,
    ):
        self.calibration = calibration or CameraCalibration()
        self.width = int(image_width)
        self.height = int(image_height)
        self._class_sizes = dict(DEFAULT_CLASS_SIZES_M)
        self._class_sizes.update(self.calibration.class_sizes_m)
        self._homography: Optional[np.ndarray] = None
        self._recompute_intrinsics()
        self._recompute_homography()

    # -- intrinsics --------------------------------------------------------

    def _recompute_intrinsics(self) -> None:
        hfov_rad = math.radians(self.calibration.hfov_deg)
        self.fx = (self.width / 2.0) / math.tan(hfov_rad / 2.0)
        self.fy = self.fx  # square pixels
        self.cx = self.width / 2.0
        self.cy = self.height / 2.0

    def update_resolution(self, width: int, height: int) -> None:
        width = int(width)
        height = int(height)
        if width <= 0 or height <= 0:
            return
        if width == self.width and height == self.height:
            return
        self.width = width
        self.height = height
        self._recompute_intrinsics()
        self._recompute_homography()

    # -- homography --------------------------------------------------------

    def _recompute_homography(self) -> None:
        self._homography = None
        cal = self.calibration
        if not cal.has_homography:
            return
        src = np.array(
            [[p[0] * self.width, p[1] * self.height] for p in cal.homography_image_points],
            dtype=np.float64,
        )
        dst = np.array(cal.homography_world_points, dtype=np.float64)
        try:
            matrix, _ = cv2.findHomography(src, dst, method=0)
        except cv2.error:
            matrix = None
        if matrix is not None and np.all(np.isfinite(matrix)):
            self._homography = matrix

    @property
    def has_ground_plane(self) -> bool:
        return self._homography is not None

    def foot_to_ground(self, u: float, v: float) -> Optional[GroundPoint]:
        """Map an image foot point (pixels) to metric ground coordinates."""
        if self._homography is None:
            return None
        pt = np.array([[[float(u), float(v)]]], dtype=np.float64)
        out = cv2.perspectiveTransform(pt, self._homography)[0][0]
        if not np.all(np.isfinite(out)):
            return None
        return GroundPoint(float(out[0]), float(out[1]))

    # -- distance ----------------------------------------------------------

    def class_size_m(self, class_label: str) -> Optional[float]:
        entry = self._class_sizes.get(str(class_label).lower())
        if not entry:
            return None
        return entry.get("height") or entry.get("width") or entry.get("length")

    def estimate_distance(
        self,
        class_label: str,
        bbox_w: float,
        bbox_h: float,
    ) -> Optional[float]:
        """Range to object along the line of sight, in metres.

        R = f * real_size / projected_size_px. Uses bbox height against the
        class height prior. Returns ``None`` for unknown classes or degenerate
        boxes. Truncated boxes (touching the frame edge) will under-report
        pixel height and therefore over-estimate range; callers can check
        ``is_truncated``.
        """
        size_m = self.class_size_m(class_label)
        if size_m is None:
            return None
        px = float(bbox_h)
        if px <= 1.0:
            return None
        range_m = (self.fy * size_m) / px
        return float(np.clip(range_m, MIN_RANGE_M, MAX_RANGE_M))

    def is_truncated(self, bbox_x: float, bbox_y: float, bbox_w: float, bbox_h: float, margin: int = 2) -> bool:
        return (
            bbox_y <= margin
            or (bbox_y + bbox_h) >= (self.height - margin)
        )

    def pixel_to_angles(self, u: float, v: float) -> tuple[float, float]:
        """Azimuth (right +) and elevation (up +) in radians for a pixel."""
        az = math.atan2(float(u) - self.cx, self.fx)
        el = -math.atan2(float(v) - self.cy, self.fy)
        return az, el

    def approx_ground_point(self, class_label: str, bbox: tuple[float, float, float, float]) -> Optional[GroundPoint]:
        """Ground position relative to the camera when no homography exists.

        Uses pinhole range and azimuth to place the object on a flat ground
        plane in a camera-centred frame (x right, y forward). Coarse but
        consistent, so inter-object distances remain useful.
        """
        x, y, w, h = bbox
        range_m = self.estimate_distance(class_label, w, h)
        if range_m is None:
            return None
        az, _ = self.pixel_to_angles(x + w / 2.0, y + h)
        return GroundPoint(range_m * math.sin(az), range_m * math.cos(az))

    def locate(self, class_label: str, bbox: tuple[float, float, float, float]) -> dict:
        """Full localisation bundle for a detection.

        Returns keys: ``distance_m``, ``ground_x_m``, ``ground_y_m``,
        ``ground_source`` ('homography' | 'pinhole' | None), ``truncated``.
        """
        x, y, w, h = (float(v) for v in bbox)
        foot_u = x + w / 2.0
        foot_v = y + h
        distance = self.estimate_distance(class_label, w, h)
        ground: Optional[GroundPoint] = None
        source: Optional[str] = None
        if self._homography is not None:
            ground = self.foot_to_ground(foot_u, foot_v)
            source = "homography" if ground else None
        if ground is None:
            ground = self.approx_ground_point(class_label, (x, y, w, h))
            source = "pinhole" if ground else None
        return {
            "distance_m": round(distance, 2) if distance is not None else None,
            "ground_x_m": round(ground.x_m, 2) if ground else None,
            "ground_y_m": round(ground.y_m, 2) if ground else None,
            "ground_source": source,
            "truncated": self.is_truncated(x, y, w, h),
        }


def ground_distance(a: dict, b: dict) -> Optional[float]:
    """Metric distance between two ``locate()`` bundles (same camera)."""
    ax, ay = a.get("ground_x_m"), a.get("ground_y_m")
    bx, by = b.get("ground_x_m"), b.get("ground_y_m")
    if None in (ax, ay, bx, by):
        return None
    return math.hypot(float(ax) - float(bx), float(ay) - float(by))
