"""ROI configuration and post-inference intrusion filtering.

Zones are polygons with semantics:

* ``zone_type`` — restricted | perimeter | entrance | driveway | parking | public.
  The risk engine uses this to decide how suspicious presence is.
* ``armed_schedule`` — when the zone is armed. ``{"mode": "always"}`` (default),
  ``{"mode": "never"}`` or ``{"mode": "schedule", "windows": [...], "tz": "..."}``.
* ``allowed_classes`` — classes that are *expected* in the zone (e.g. ``car`` in
  a driveway). They never count as intruders there.

Dwell evaluation uses the *foot point* (bottom-centre of the bbox) so that
ground-plane zones behave intuitively, keeps per-track state alive across
short tracking dropouts, and groups per-track threshold crossings into
zone-level *incidents* so operators get one alert per event, not one per
re-identified track.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cv2
import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

ZONE_TYPES = ("restricted", "perimeter", "entrance", "driveway", "parking", "public")
DEFAULT_ZONE_TYPE = "restricted"


def _backend_root() -> Path:
    # .../backend/yolo_classifier/app/detection/roi.py -> .../backend
    return Path(__file__).resolve().parents[3]


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return _backend_root() / path


# ---------------------------------------------------------------------------
# Arming schedule
# ---------------------------------------------------------------------------


def _parse_hhmm(value: Any) -> Optional[dt_time]:
    if isinstance(value, dt_time):
        return value
    try:
        text = str(value).strip()
        hours, minutes = text.split(":")
        return dt_time(hour=int(hours), minute=int(minutes))
    except (ValueError, AttributeError):
        return None


@dataclass
class ArmedSchedule:
    mode: str = "always"  # always | never | schedule
    windows: list[dict] = field(default_factory=list)
    tz: str = "UTC"

    @classmethod
    def from_dict(cls, payload: Any) -> "ArmedSchedule":
        if payload is None:
            return cls()
        if isinstance(payload, str):
            return cls(mode=payload if payload in ("always", "never") else "always")
        if not isinstance(payload, dict):
            return cls()
        mode = str(payload.get("mode", "always")).lower()
        if mode not in ("always", "never", "schedule"):
            mode = "always"
        windows = []
        for raw in payload.get("windows", []) or []:
            if not isinstance(raw, dict):
                continue
            start = _parse_hhmm(raw.get("start"))
            end = _parse_hhmm(raw.get("end"))
            if start is None or end is None:
                continue
            days = raw.get("days")
            if days is None:
                days = list(range(7))
            windows.append(
                {
                    "start": start.strftime("%H:%M"),
                    "end": end.strftime("%H:%M"),
                    "days": sorted({int(d) for d in days if 0 <= int(d) <= 6}),
                }
            )
        return cls(mode=mode, windows=windows, tz=str(payload.get("tz", "UTC")))

    def to_dict(self) -> dict:
        return {"mode": self.mode, "windows": list(self.windows), "tz": self.tz}

    def is_armed(self, now: Optional[datetime] = None) -> bool:
        if self.mode == "always":
            return True
        if self.mode == "never":
            return False
        if not self.windows:
            return False
        try:
            tzinfo = ZoneInfo(self.tz)
        except (ZoneInfoNotFoundError, ValueError):
            tzinfo = timezone.utc
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        local = moment.astimezone(tzinfo)
        weekday = local.weekday()
        current = local.time().replace(second=0, microsecond=0)
        for window in self.windows:
            start = _parse_hhmm(window["start"])
            end = _parse_hhmm(window["end"])
            if start is None or end is None:
                continue
            days = window.get("days") or list(range(7))
            if start <= end:
                if weekday in days and start <= current < end:
                    return True
            else:
                # Overnight window, e.g. 22:00 -> 06:00.
                prev_day = (weekday - 1) % 7
                if (weekday in days and current >= start) or (prev_day in days and current < end):
                    return True
        return False


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


@dataclass
class RoiZone:
    zone_id: int
    name: str
    normalized_points: np.ndarray  # shape: (N, 2) in [0,1]
    threshold_sec: float
    color: tuple[int, int, int]
    camera_ids: Optional[set[str]] = None
    zone_type: str = DEFAULT_ZONE_TYPE
    armed_schedule: ArmedSchedule = field(default_factory=ArmedSchedule)
    allowed_classes: set[str] = field(default_factory=set)

    def applies_to_camera(self, camera_id: str) -> bool:
        return not self.camera_ids or camera_id in self.camera_ids

    def is_armed(self, now: Optional[datetime] = None) -> bool:
        return self.armed_schedule.is_armed(now)

    def to_pixel_points(self, frame_width: int, frame_height: int) -> np.ndarray:
        points = np.zeros_like(self.normalized_points, dtype=np.float32)
        points[:, 0] = self.normalized_points[:, 0] * float(frame_width)
        points[:, 1] = self.normalized_points[:, 1] * float(frame_height)
        return points

    def contains_pixel(self, x: float, y: float, frame_width: int, frame_height: int) -> bool:
        polygon = self.to_pixel_points(frame_width, frame_height).reshape((-1, 1, 2))
        return cv2.pointPolygonTest(polygon, (float(x), float(y)), False) >= 0

    def to_payload(self, now: Optional[datetime] = None, armed: Optional[bool] = None) -> dict:
        return {
            "zone_id": self.zone_id,
            "name": self.name,
            "points": [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in self.normalized_points],
            "threshold_sec": self.threshold_sec,
            "color": list(self.color),
            "zone_type": self.zone_type,
            "armed": self.is_armed(now) if armed is None else bool(armed),
            "armed_schedule": self.armed_schedule.to_dict(),
            "allowed_classes": sorted(self.allowed_classes),
        }


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
    zone_type: str = DEFAULT_ZONE_TYPE
    incident_id: str = ""
    new_incident: bool = True
    intruder_count: int = 1

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "object_id": self.object_id,
            "class_label": self.class_label,
            "zone_id": self.zone_id,
            "zone_name": self.zone_name,
            "zone_type": self.zone_type,
            "dwell_seconds": round(self.dwell_seconds, 2),
            "threshold_seconds": round(self.threshold_seconds, 2),
            "timestamp_unix": round(self.timestamp_unix, 6),
            "incident_id": self.incident_id,
            "new_incident": self.new_incident,
            "intruder_count": self.intruder_count,
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
            zones.append(
                parse_zone(
                    raw_zone,
                    next_id=len(zones) + 1,
                    default_threshold=self._default_threshold,
                    default_reference_width=default_reference_width,
                    default_reference_height=default_reference_height,
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


def parse_zone(
    raw_zone: dict,
    *,
    next_id: int,
    default_threshold: float,
    default_reference_width: int,
    default_reference_height: int,
) -> RoiZone:
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
    zone_type = str(raw_zone.get("zone_type", DEFAULT_ZONE_TYPE)).lower()
    if zone_type not in ZONE_TYPES:
        zone_type = DEFAULT_ZONE_TYPE
    allowed = {str(c).lower() for c in (raw_zone.get("allowed_classes") or [])}

    return RoiZone(
        zone_id=int(raw_zone.get("zone_id", next_id)),
        name=str(raw_zone.get("name", f"Zone-{next_id}")),
        normalized_points=normalized,
        threshold_sec=float(raw_zone.get("threshold_sec", default_threshold)),
        color=color,
        camera_ids=camera_ids,
        zone_type=zone_type,
        armed_schedule=ArmedSchedule.from_dict(raw_zone.get("armed_schedule")),
        allowed_classes=allowed,
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
            "zone_type": DEFAULT_ZONE_TYPE,
            "armed_schedule": {"mode": "always"},
            "reference_width": settings.ROI_REFERENCE_WIDTH,
            "reference_height": settings.ROI_REFERENCE_HEIGHT,
        }
    ]

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


# ---------------------------------------------------------------------------
# Intrusion filter
# ---------------------------------------------------------------------------


@dataclass
class _TrackZoneState:
    entered_at: float
    last_seen: float
    flagged: bool = False
    last_event_time: float = 0.0


@dataclass
class _ZoneIncident:
    incident_id: str
    opened_at: float
    last_activity: float
    track_ids: set[int] = field(default_factory=set)


TRUNCATION_MARGIN_PX = 4.0


def anchor_point(
    obj: dict,
    anchor: str = "foot",
    frame_shape: Optional[tuple[int, ...]] = None,
) -> tuple[float, float]:
    """Reference point used for zone membership tests.

    ``foot`` is the bottom-centre of the bbox — where the person touches the
    ground — which is what a zone polygon drawn on the floor means. When the
    bbox is cut off by the bottom frame edge (person too close to the camera,
    feet out of view) the true foot is unknown, so fall back to the bbox
    centre instead of testing a point that lies on the frame border.
    """
    bbox_x = float(obj.get("bbox_x", 0.0))
    bbox_y = float(obj.get("bbox_y", 0.0))
    bbox_w = float(obj.get("bbox_w", 0.0))
    bbox_h = float(obj.get("bbox_h", 0.0))
    cx = bbox_x + bbox_w / 2.0
    cy = bbox_y + bbox_h / 2.0
    if anchor == "center":
        return cx, cy
    foot_y = bbox_y + bbox_h
    if frame_shape is not None and len(frame_shape) >= 2:
        frame_h = float(frame_shape[0])
        if foot_y >= frame_h - TRUNCATION_MARGIN_PX:
            return cx, cy
    return cx, foot_y


class RoiIntrusionFilter:
    """Evaluates tracked detections against configured ROI polygons.

    Behavioural guarantees:

    * A tracked object inside an *armed* zone for ``threshold_sec`` becomes an
      intrusion for that zone.
    * Dwell state survives tracking dropouts shorter than ``track_grace_sec``.
    * Threshold crossings are grouped into zone incidents. ``new_incident`` is
      True only on the first crossing of an incident; subsequent tracks joining
      the same incident (or the same track being re-identified) produce events
      with ``new_incident=False`` so downstream alerting can de-duplicate.
    * Objects listed in the zone's ``allowed_classes`` never intrude there.
    * Zones that are not armed still report ``inside_roi``/dwell so the risk
      engine can reason about presence, but never emit intrusion events.
    """

    def __init__(
        self,
        camera_id: str,
        zone_repository: Optional[RoiZoneRepository] = None,
        intruder_classes: Optional[Iterable[str]] = None,
        alert_cooldown_sec: Optional[float] = None,
        track_grace_sec: Optional[float] = None,
        incident_holddown_sec: Optional[float] = None,
        anchor: Optional[str] = None,
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
        self._track_grace_sec = (
            float(track_grace_sec)
            if track_grace_sec is not None
            else float(settings.ROI_TRACK_GRACE_SEC)
        )
        self._incident_holddown_sec = (
            float(incident_holddown_sec)
            if incident_holddown_sec is not None
            else float(settings.ROI_INCIDENT_HOLDDOWN_SEC)
        )
        self._anchor = (anchor or settings.ROI_ANCHOR).lower()

        self._states: dict[tuple[int, int], _TrackZoneState] = {}
        self._incidents: dict[int, _ZoneIncident] = {}

    # -- public API ---------------------------------------------------------

    def zones(self) -> list[RoiZone]:
        return self._repository.zones_for_camera(self._camera_id)

    def zones_payload(self, now: Optional[datetime] = None, arm_mode: str = "auto") -> list[dict]:
        override = {"armed": True, "disarmed": False}.get(arm_mode)
        return [zone.to_payload(now, armed=override) for zone in self.zones()]

    def evaluate(
        self,
        tracked_objects: list[dict],
        frame_shape: tuple[int, int] | tuple[int, int, int],
        timestamp: Optional[float] = None,
        now_dt: Optional[datetime] = None,
        arm_mode: str = "auto",
    ) -> list[IntrusionEvent]:
        """Evaluate one frame.

        ``arm_mode`` is the site-wide override: ``armed`` forces every zone
        armed, ``disarmed`` forces every zone disarmed, ``auto`` defers to each
        zone's schedule.
        """
        if not self._enabled:
            self.reset()
            for obj in tracked_objects:
                self._annotate_empty(obj)
            return []

        now = timestamp if timestamp is not None else time.time()
        wall = now_dt or datetime.fromtimestamp(now, tz=timezone.utc)
        frame_height = int(frame_shape[0])
        frame_width = int(frame_shape[1])
        zones = self.zones()
        if arm_mode == "armed":
            armed_by_zone = {zone.zone_id: True for zone in zones}
        elif arm_mode == "disarmed":
            armed_by_zone = {zone.zone_id: False for zone in zones}
        else:
            armed_by_zone = {zone.zone_id: zone.is_armed(wall) for zone in zones}

        events: list[IntrusionEvent] = []
        touched_keys: set[tuple[int, int]] = set()

        for obj in tracked_objects:
            track_id = int(obj.get("object_id", -1))
            if track_id < 0:
                continue

            class_label = str(obj.get("class_label", "")).lower()
            ax, ay = anchor_point(obj, self._anchor, frame_shape)

            zone_ids_inside: list[int] = []
            zone_types_inside: list[str] = []
            armed_zone_ids: list[int] = []
            max_dwell = 0.0
            is_intrusion = False

            for zone in zones:
                key = (zone.zone_id, track_id)
                if not zone.contains_pixel(ax, ay, frame_width, frame_height):
                    continue

                zone_ids_inside.append(zone.zone_id)
                zone_types_inside.append(zone.zone_type)
                if armed_by_zone[zone.zone_id]:
                    armed_zone_ids.append(zone.zone_id)

                eligible = class_label in self._intruder_classes and class_label not in zone.allowed_classes
                if not eligible:
                    continue

                state = self._states.get(key)
                if state is None:
                    state = _TrackZoneState(entered_at=now, last_seen=now)
                    self._states[key] = state
                state.last_seen = now
                touched_keys.add(key)

                dwell = now - state.entered_at
                max_dwell = max(max_dwell, dwell)

                if not armed_by_zone[zone.zone_id]:
                    state.flagged = False
                    continue

                if dwell >= zone.threshold_sec:
                    is_intrusion = True
                    incident, new_incident = self._touch_incident(zone.zone_id, track_id, now)
                    should_emit = (
                        not state.flagged
                        or (now - state.last_event_time >= self._alert_cooldown_sec)
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
                                zone_type=zone.zone_type,
                                incident_id=incident.incident_id,
                                new_incident=new_incident,
                                intruder_count=len(incident.track_ids),
                            )
                        )
                        state.last_event_time = now
                    state.flagged = True
                else:
                    state.flagged = False

            obj["inside_roi"] = bool(zone_ids_inside)
            obj["intrusion"] = is_intrusion
            obj["roi_zone_ids"] = zone_ids_inside
            obj["roi_zone_types"] = zone_types_inside
            obj["roi_armed_zone_ids"] = armed_zone_ids
            obj["max_roi_dwell_sec"] = round(max_dwell, 2)
            obj["anchor_x"] = round(ax, 1)
            obj["anchor_y"] = round(ay, 1)

        self._expire_states(now, touched_keys)
        self._expire_incidents(now)
        return events

    def active_incidents(self) -> dict[int, dict]:
        return {
            zone_id: {
                "incident_id": inc.incident_id,
                "opened_at": inc.opened_at,
                "last_activity": inc.last_activity,
                "track_ids": sorted(inc.track_ids),
            }
            for zone_id, inc in self._incidents.items()
        }

    def reset(self) -> None:
        self._states.clear()
        self._incidents.clear()

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _annotate_empty(obj: dict) -> None:
        obj["inside_roi"] = False
        obj["intrusion"] = False
        obj["roi_zone_ids"] = []
        obj["roi_zone_types"] = []
        obj["roi_armed_zone_ids"] = []
        obj["max_roi_dwell_sec"] = 0.0

    def _touch_incident(self, zone_id: int, track_id: int, now: float) -> tuple[_ZoneIncident, bool]:
        incident = self._incidents.get(zone_id)
        new = False
        if incident is None:
            incident = _ZoneIncident(
                incident_id=uuid.uuid4().hex[:12],
                opened_at=now,
                last_activity=now,
            )
            self._incidents[zone_id] = incident
            new = True
        incident.last_activity = now
        incident.track_ids.add(track_id)
        return incident, new

    def _expire_states(self, now: float, touched_keys: set[tuple[int, int]]) -> None:
        for key in list(self._states.keys()):
            if key in touched_keys:
                continue
            state = self._states[key]
            if now - state.last_seen > self._track_grace_sec:
                del self._states[key]

    def _expire_incidents(self, now: float) -> None:
        for zone_id in list(self._incidents.keys()):
            incident = self._incidents[zone_id]
            if now - incident.last_activity > self._incident_holddown_sec:
                del self._incidents[zone_id]
