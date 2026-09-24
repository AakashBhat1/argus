"""Monocular camera geometry: distance estimation and ground-plane mapping.

Ported from the drone interceptor ``CameraGeometry`` (pinhole + known object
size) and extended for fixed surveillance cameras:

* Per-class real-world size table (person height, car height, ...).
* Optional ground-plane homography calibrated from four image points with
  known real-world spacing (e.g. a parking bay or a measured rectangle).
  When present it yields metric ground coordinates for every foot point,
  which makes inter-object distances and speeds meaningful.
* Camera pose recovered from that same calibration (planar PnP), so ranges
  are measured from the camera's own ground position. The calibration
  rectangle's origin is arbitrary (often a bay corner) and must never be
  used as the camera position.

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


def _positive_float(value: Any, upper: float) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0.0 or number > upper:
        return None
    return number


@dataclass(frozen=True)
class CameraPose:
    """Camera position in the calibration's ground frame."""

    ground_x_m: float
    ground_y_m: float
    height_m: Optional[float]
    focal_px: Optional[float]
    source: str  # "measured" | "pnp"
    reprojection_error_px: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "ground_x_m": round(self.ground_x_m, 3),
            "ground_y_m": round(self.ground_y_m, 3),
            "height_m": round(self.height_m, 3) if self.height_m is not None else None,
            "focal_px": round(self.focal_px, 2) if self.focal_px is not None else None,
            "source": self.source,
            "reprojection_error_px": (
                round(self.reprojection_error_px, 3)
                if self.reprojection_error_px is not None
                else None
            ),
        }


@dataclass
class CameraCalibration:
    """Serializable calibration payload stored on ``Camera.calibration``."""

    hfov_deg: float = 84.0
    # Optional ground-plane homography inputs (normalized image coords 0..1).
    homography_image_points: list[list[float]] = field(default_factory=list)
    # Matching real-world points in metres (any planar frame, e.g. bay corners).
    homography_world_points: list[list[float]] = field(default_factory=list)
    class_sizes_m: dict[str, dict[str, float]] = field(default_factory=dict)
    # Optional measured camera mounting height above the ground plane (m).
    camera_height_m: Optional[float] = None
    # Optional measured camera ground position [x, y] in the calibration
    # frame. When absent the position is recovered from the calibration.
    camera_ground_position_m: Optional[list[float]] = None

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
        height = _positive_float(payload.get("camera_height_m"), upper=200.0)
        position = payload.get("camera_ground_position_m")
        try:
            position = (
                [float(position[0]), float(position[1])]
                if isinstance(position, (list, tuple)) and len(position) >= 2
                and all(math.isfinite(float(v)) for v in position[:2])
                else None
            )
        except (TypeError, ValueError):
            position = None
        return cls(
            hfov_deg=hfov,
            homography_image_points=[[float(p[0]), float(p[1])] for p in img_pts if len(p) >= 2],
            homography_world_points=[[float(p[0]), float(p[1])] for p in world_pts if len(p) >= 2],
            class_sizes_m={
                str(k).lower(): {str(dk): float(dv) for dk, dv in v.items()}
                for k, v in sizes.items()
                if isinstance(v, dict)
            },
            camera_height_m=height,
            camera_ground_position_m=position,
        )

    def to_dict(self) -> dict:
        return {
            "hfov_deg": self.hfov_deg,
            "homography_image_points": self.homography_image_points,
            "homography_world_points": self.homography_world_points,
            "class_sizes_m": self.class_sizes_m,
            "camera_height_m": self.camera_height_m,
            "camera_ground_position_m": self.camera_ground_position_m,
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
        self._pose: Optional[CameraPose] = None
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
        self._pose = self._recover_pose(src, dst) if self._homography is not None else None

    # -- camera pose -------------------------------------------------------

    def _focal_from_homography(self, image_to_world: np.ndarray) -> Optional[float]:
        """Self-calibrate the focal length from the ground homography.

        With square pixels and a centred principal point, the two rotation
        columns recovered from ``K^-1 H`` must be orthogonal and of equal
        norm; each constraint yields an estimate of f^2. Returns ``None`` when
        the view is too close to fronto-parallel for a stable estimate.
        """
        try:
            world_to_image = np.linalg.inv(image_to_world)
        except np.linalg.LinAlgError:
            return None
        shift = np.array([[1.0, 0.0, -self.cx], [0.0, 1.0, -self.cy], [0.0, 0.0, 1.0]])
        a = shift @ world_to_image
        a = a / np.linalg.norm(a[:, :2])
        estimates: list[tuple[float, float]] = []
        den1 = a[2, 0] * a[2, 1]
        if abs(den1) > 1e-9:
            estimates.append((-(a[0, 0] * a[0, 1] + a[1, 0] * a[1, 1]) / den1, abs(den1)))
        den2 = a[2, 0] ** 2 - a[2, 1] ** 2
        if abs(den2) > 1e-9:
            estimates.append(
                ((a[0, 1] ** 2 + a[1, 1] ** 2 - a[0, 0] ** 2 - a[1, 0] ** 2) / den2, abs(den2))
            )
        valid = [(f2, w) for f2, w in estimates if math.isfinite(f2) and f2 > 0]
        if not valid:
            return None
        focal = math.sqrt(sum(f2 * w for f2, w in valid) / sum(w for _, w in valid))
        hfov = math.degrees(2.0 * math.atan((self.width / 2.0) / focal))
        return focal if 15.0 <= hfov <= 160.0 else None

    def _solve_pose(
        self, image_px: np.ndarray, world_m: np.ndarray, focal: float
    ) -> Optional[tuple[np.ndarray, float]]:
        object_pts = np.hstack([world_m, np.zeros((len(world_m), 1))]).astype(np.float64)
        k = np.array([[focal, 0.0, self.cx], [0.0, focal, self.cy], [0.0, 0.0, 1.0]])
        dist = np.zeros(4)
        flag = cv2.SOLVEPNP_IPPE if len(world_m) >= 4 else cv2.SOLVEPNP_ITERATIVE
        try:
            ok, rvec, tvec = cv2.solvePnP(object_pts, image_px, k, dist, flags=flag)
        except cv2.error:
            return None
        if not ok:
            return None
        rotation, _ = cv2.Rodrigues(rvec)
        cam_frame = (rotation @ object_pts.T + tvec).T
        if np.any(cam_frame[:, 2] <= 0):
            return None  # calibration points behind the camera: bad solution
        projected, _ = cv2.projectPoints(object_pts, rvec, tvec, k, dist)
        error = float(np.mean(np.linalg.norm(projected.reshape(-1, 2) - image_px, axis=1)))
        centre = (-rotation.T @ tvec).reshape(3)
        if not (math.isfinite(error) and np.all(np.isfinite(centre))):
            return None
        return centre, error

    def _recover_pose(self, image_px: np.ndarray, world_m: np.ndarray) -> Optional[CameraPose]:
        cal = self.calibration
        candidates: list[tuple[float, np.ndarray, float]] = []
        focal_options = [self.fx]
        self_calibrated = self._focal_from_homography(self._homography)
        if self_calibrated is not None:
            focal_options.append(self_calibrated)
        for focal in focal_options:
            solved = self._solve_pose(image_px, world_m, focal)
            if solved is not None:
                centre, error = solved
                candidates.append((error, centre, focal))
        best = min(candidates, key=lambda item: item[0]) if candidates else None
        max_error = max(2.0, 0.01 * self.width)

        pnp_height = abs(float(best[1][2])) if best is not None else None
        height = cal.camera_height_m if cal.camera_height_m is not None else pnp_height
        if cal.camera_ground_position_m is not None:
            return CameraPose(
                ground_x_m=float(cal.camera_ground_position_m[0]),
                ground_y_m=float(cal.camera_ground_position_m[1]),
                height_m=height,
                focal_px=best[2] if best is not None else None,
                source="measured",
                reprojection_error_px=best[0] if best is not None else None,
            )
        if best is None or best[0] > max_error:
            return None
        return CameraPose(
            ground_x_m=float(best[1][0]),
            ground_y_m=float(best[1][1]),
            height_m=height,
            focal_px=best[2],
            source="pnp",
            reprojection_error_px=best[0],
        )

    @property
    def camera_pose(self) -> Optional[CameraPose]:
        return self._pose

    @property
    def has_ground_plane(self) -> bool:
        return self._homography is not None

    def range_from_camera(self, point: "GroundPoint") -> Optional[float]:
        """Horizontal ground range from the camera's foot point to ``point``."""
        if self._pose is None:
            return None
        return math.hypot(point.x_m - self._pose.ground_x_m, point.y_m - self._pose.ground_y_m)

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
            bbox_x <= margin
            or bbox_y <= margin
            or (bbox_x + bbox_w) >= (self.width - margin)
            or (bbox_y + bbox_h) >= (self.height - margin)
        )

    def pixel_to_angles(self, u: float, v: float) -> tuple[float, float]:
        """Azimuth (right +) and elevation (up +) in radians for a pixel."""
        az = math.atan2(float(u) - self.cx, self.fx)
        el = -math.atan2(float(v) - self.cy, self.fy)
        return az, el

    def locate(self, class_label: str, bbox: tuple[float, float, float, float]) -> dict:
        """Full localisation bundle for a detection.

        Returns keys: ``distance_m`` (horizontal range from the camera's
        ground position), ``ground_x_m``, ``ground_y_m``, ``ground_source``
        ('homography' | None), ``distance_source`` ('ground_plane' | None) and
        ``truncated``. Metric localization is deliberately unavailable until a
        ground plane is calibrated; bbox-height priors are too pose-sensitive
        for safety use. Range additionally needs a recoverable camera pose.
        """
        x, y, w, h = (float(v) for v in bbox)
        foot_u = x + w / 2.0
        foot_v = y + h
        truncated = self.is_truncated(x, y, w, h)
        distance = None
        ground: Optional[GroundPoint] = None
        source: Optional[str] = None
        if self._homography is not None and not truncated:
            ground = self.foot_to_ground(foot_u, foot_v)
            source = "homography" if ground else None
            if ground is not None:
                # Measured from the camera, not from the calibration origin
                # (which is typically an arbitrary bay corner).
                distance = self.range_from_camera(ground)
        return {
            "distance_m": round(distance, 2) if distance is not None else None,
            "distance_source": "ground_plane" if distance is not None else None,
            "ground_x_m": round(ground.x_m, 2) if ground else None,
            "ground_y_m": round(ground.y_m, 2) if ground else None,
            "ground_source": source,
            "truncated": truncated,
        }


def ground_distance(a: dict, b: dict) -> Optional[float]:
    """Metric distance between two ``locate()`` bundles (same camera)."""
    ax, ay = a.get("ground_x_m"), a.get("ground_y_m")
    bx, by = b.get("ground_x_m"), b.get("ground_y_m")
    if None in (ax, ay, bx, by):
        return None
    return math.hypot(float(ax) - float(bx), float(ay) - float(by))
