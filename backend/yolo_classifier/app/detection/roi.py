"""ROI configuration and post-inference intrusion filtering."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)


def _backend_root() -> Path:
    # .../backend/yolo_classifier/app/detection/roi.py -> .../backend
    return Path(__file__).resolve().parents[3]


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return _backend_root() / path


@dataclass
class RoiZone:
    zone_id: int
    name: str
    normalized_points: np.ndarray  # shape: (N, 2) in [0,1]
    threshold_sec: float
    color: tuple[int, int, int]
    camera_ids: Optional[set[str]] = None

    def applies_to_camera(self, camera_id: str) -> bool:
        return not self.camera_ids or camera_id in self.camera_ids

    def to_pixel_points(self, frame_width: int, frame_height: int) -> np.ndarray:
        points = np.zeros_like(self.normalized_points, dtype=np.float32)
        points[:, 0] = self.normalized_points[:, 0] * float(frame_width)
        points[:, 1] = self.normalized_points[:, 1] * float(frame_height)
        return points

    def contains_pixel(self, x: float, y: float, frame_width: int, frame_height: int) -> bool:
        polygon = self.to_pixel_points(frame_width, frame_height).reshape((-1, 1, 2))
        return cv2.pointPolygonTest(polygon, (float(x), float(y)), False) >= 0


@dataclass
class IntrusionEvent:
    camera_id: str
    object_id: int
    class_label: str
    zone_id: int
    zone_name: str
    dwell_seconds: float
    threshold_seconds: float
    timestamp_unix: float

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "object_id": self.object_id,
            "class_label": self.class_label,
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "dwell_seconds": round(self.dwell_seconds, 2),
            "threshold_seconds": round(self.threshold_seconds, 2),
            "timestamp_unix": round(self.timestamp_unix, 6),
        }


class RoiZoneRepository:
    """Loads and caches ROI zones from configuration."""

    def __init__(self, config_path: Optional[str] = None):
        settings = get_settings()
        self._config_path = _resolve_path(config_path or settings.ROI_ZONES_CONFIG_PATH)
        self._reference_width = settings.ROI_REFERENCE_WIDTH
        self._reference_height = settings.ROI_REFERENCE_HEIGHT
        self._default_threshold = settings.ROI_DEFAULT_DWELL_SEC
        self._cached_zones: list[RoiZone] = []
        self._cached_mtime: Optional[float] = None

    @property
    def config_path(self) -> Path:
        return self._config_path

    def zones_for_camera(self, camera_id: str) -> list[RoiZone]:
        self._refresh_cache_if_needed()
        return [zone for zone in self._cached_zones if zone.applies_to_camera(camera_id)]

    def all_zones(self) -> list[RoiZone]:
        self._refresh_cache_if_needed()
        return list(self._cached_zones)

    def _refresh_cache_if_needed(self) -> None:
        if not self._config_path.exists():
            if not self._cached_zones:
                self._cached_zones = [self._default_zone()]
                logger.info("ROI config not found at %s. Using default zone.", self._config_path)
            return

        mtime = self._config_path.stat().st_mtime
        if self._cached_mtime is not None and mtime == self._cached_mtime:
            return

        try:
            with open(self._config_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            zones = self._parse_payload(payload)
            self._cached_zones = zones if zones else [self._default_zone()]
            self._cached_mtime = mtime
            logger.info("Loaded %s ROI zones from %s", len(self._cached_zones), self._config_path)
        except Exception as exc:
            logger.error("Failed to parse ROI config at %s: %s", self._config_path, exc)
            self._cached_zones = [self._default_zone()]

    def _parse_payload(self, payload: object) -> list[RoiZone]:
        if isinstance(payload, dict):
            zones_data = payload.get("zones", [])
            default_reference_width = int(payload.get("reference_width", self._reference_width))
            default_reference_height = int(payload.get("reference_height", self._reference_height))
        elif isinstance(payload, list):
            zones_data = payload
            default_reference_width = self._reference_width
            default_reference_height = self._reference_height
        else:
            raise ValueError("ROI config must be a list or an object with 'zones'.")

        zones: list[RoiZone] = []
        for raw_zone in zones_data:
            points_raw = np.array(raw_zone["points"], dtype=np.float32)
            if points_raw.ndim != 2 or points_raw.shape[1] != 2:
                raise ValueError(f"Invalid points for zone: {raw_zone}")

            max_val = float(points_raw.max()) if points_raw.size else 0.0
            if max_val <= 1.0:
                normalized = points_raw
            else:
                ref_width = float(raw_zone.get("reference_width", default_reference_width))
                ref_height = float(raw_zone.get("reference_height", default_reference_height))
                if ref_width <= 0 or ref_height <= 0:
                    raise ValueError("reference_width/reference_height must be > 0")
                normalized = np.column_stack((points_raw[:, 0] / ref_width, points_raw[:, 1] / ref_height))

            normalized = np.clip(normalized, 0.0, 1.0).astype(np.float32)

            camera_ids_raw = raw_zone.get("camera_ids")
            camera_ids = set(map(str, camera_ids_raw)) if camera_ids_raw else None
            color_raw = raw_zone.get("color", [0, 255, 255])
            color = (
                int(color_raw[0]) if len(color_raw) > 0 else 0,
                int(color_raw[1]) if len(color_raw) > 1 else 255,
                int(color_raw[2]) if len(color_raw) > 2 else 255,
            )

            zones.append(
                RoiZone(
                    zone_id=int(raw_zone.get("zone_id", len(zones) + 1)),
                    name=str(raw_zone.get("name", f"Zone-{len(zones) + 1}")),
                    normalized_points=normalized,
                    threshold_sec=float(raw_zone.get("threshold_sec", self._default_threshold)),
                    color=color,
                    camera_ids=camera_ids,
                )
            )

        return zones

    def _default_zone(self) -> RoiZone:
        # Default to the same central zone shape used by the legacy ROI script.
        points = np.array(
            [
                [0.25, 0.25],
                [0.75, 0.25],
                [0.75, 0.80],
                [0.25, 0.80],
            ],
            dtype=np.float32,
        )
        return RoiZone(
            zone_id=1,
            name="Zone-1",
            normalized_points=points,
            threshold_sec=self._default_threshold,
            color=(0, 255, 255),
            camera_ids=None,
        )


def write_default_zones_config(config_path: Optional[str] = None) -> Path:
    """Write a starter ROI zone file in a format compatible with old configs."""
    settings = get_settings()
    path = _resolve_path(config_path or settings.ROI_ZONES_CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = [
        {
            "zone_id": 1,
            "name": "Zone-1",
            "points": [
                [int(settings.ROI_REFERENCE_WIDTH * 0.25), int(settings.ROI_REFERENCE_HEIGHT * 0.25)],
                [int(settings.ROI_REFERENCE_WIDTH * 0.75), int(settings.ROI_REFERENCE_HEIGHT * 0.25)],
                [int(settings.ROI_REFERENCE_WIDTH * 0.75), int(settings.ROI_REFERENCE_HEIGHT * 0.80)],
                [int(settings.ROI_REFERENCE_WIDTH * 0.25), int(settings.ROI_REFERENCE_HEIGHT * 0.80)],
            ],
            "threshold_sec": settings.ROI_DEFAULT_DWELL_SEC,
            "color": [0, 255, 255],
            "reference_width": settings.ROI_REFERENCE_WIDTH,
            "reference_height": settings.ROI_REFERENCE_HEIGHT,
        }
    ]

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


class RoiIntrusionFilter:
    """Evaluates tracked detections against configured ROI polygons."""

    def __init__(
        self,
        camera_id: str,
        zone_repository: Optional[RoiZoneRepository] = None,
        intruder_classes: Optional[Iterable[str]] = None,
        alert_cooldown_sec: Optional[float] = None,
    ):
        settings = get_settings()
        self._enabled = bool(settings.ROI_ENABLED)
        self._camera_id = str(camera_id)
        self._repository = zone_repository or RoiZoneRepository()
        classes = intruder_classes or settings.ROI_INTRUDER_CLASSES
        self._intruder_classes = {str(item).lower() for item in classes}
        self._alert_cooldown_sec = (
            float(alert_cooldown_sec)
            if alert_cooldown_sec is not None
            else float(settings.ROI_ALERT_COOLDOWN_SEC)
        )

        self._entry_times: dict[tuple[int, int], float] = {}
        self._flagged: dict[tuple[int, int], bool] = {}
        self._last_event_time: dict[tuple[int, int], float] = {}

    def evaluate(
        self,
        tracked_objects: list[dict],
        frame_shape: tuple[int, int] | tuple[int, int, int],
        timestamp: Optional[float] = None,
    ) -> list[IntrusionEvent]:
        if not self._enabled:
            self.reset()
            for obj in tracked_objects:
                obj["inside_roi"] = False
                obj["intrusion"] = False
                obj["roi_zone_ids"] = []
                obj["max_roi_dwell_sec"] = 0.0
            return []

        now = timestamp if timestamp is not None else time.time()
        frame_height = int(frame_shape[0])
        frame_width = int(frame_shape[1])
        zones = self._repository.zones_for_camera(self._camera_id)

        active_keys: set[tuple[int, int]] = set()
        active_track_ids: set[int] = set()
        events: list[IntrusionEvent] = []

        for obj in tracked_objects:
            track_id = int(obj.get("object_id", -1))
            if track_id < 0:
                continue

            active_track_ids.add(track_id)
            class_label = str(obj.get("class_label", "")).lower()
            bbox_x = float(obj.get("bbox_x", 0.0))
            bbox_y = float(obj.get("bbox_y", 0.0))
            bbox_w = float(obj.get("bbox_w", 0.0))
            bbox_h = float(obj.get("bbox_h", 0.0))
            center_x = bbox_x + (bbox_w / 2.0)
            center_y = bbox_y + (bbox_h / 2.0)

            zone_ids_inside: list[int] = []
            max_dwell = 0.0
            is_intrusion = False

            for zone in zones:
                key = (zone.zone_id, track_id)
                if class_label not in self._intruder_classes:
                    self._clear_state(key)
                    continue

                if zone.contains_pixel(center_x, center_y, frame_width, frame_height):
                    active_keys.add(key)
                    zone_ids_inside.append(zone.zone_id)
                    start_time = self._entry_times.setdefault(key, now)
                    dwell = now - start_time
                    max_dwell = max(max_dwell, dwell)

                    if dwell >= zone.threshold_sec:
                        is_intrusion = True
                        should_emit = (
                            (not self._flagged.get(key, False))
                            or (now - self._last_event_time.get(key, 0.0) >= self._alert_cooldown_sec)
                        )
                        if should_emit:
                            events.append(
                                IntrusionEvent(
                                    camera_id=self._camera_id,
                                    object_id=track_id,
                                    class_label=class_label,
                                    zone_id=zone.zone_id,
                                    zone_name=zone.name,
                                    dwell_seconds=dwell,
                                    threshold_seconds=zone.threshold_sec,
                                    timestamp_unix=now,
                                )
                            )
                            self._last_event_time[key] = now
                        self._flagged[key] = True
                    else:
                        self._flagged[key] = False
                else:
                    self._clear_state(key)

            obj["inside_roi"] = bool(zone_ids_inside)
            obj["intrusion"] = is_intrusion
            obj["roi_zone_ids"] = zone_ids_inside
            obj["max_roi_dwell_sec"] = round(max_dwell, 2)

        self._purge_stale_state(active_track_ids=active_track_ids, active_zone_keys=active_keys)
        return events

    def reset(self) -> None:
        self._entry_times.clear()
        self._flagged.clear()
        self._last_event_time.clear()

    def _purge_stale_state(
        self,
        active_track_ids: set[int],
        active_zone_keys: set[tuple[int, int]],
    ) -> None:
        keys = set(self._entry_times.keys()) | set(self._flagged.keys()) | set(self._last_event_time.keys())
        for key in keys:
            zone_id, track_id = key
            if track_id not in active_track_ids or key not in active_zone_keys:
                self._clear_state((zone_id, track_id))

    def _clear_state(self, key: tuple[int, int]) -> None:
        self._entry_times.pop(key, None)
        self._flagged.pop(key, None)
        self._last_event_time.pop(key, None)
