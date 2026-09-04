"""Contextual risk engine.

Turns the binary "person inside polygon for N seconds" rule into a graded
risk score per tracked person, computed online every frame from:

* **Zone semantics** — what kind of zone the person stands in and whether
  it is armed right now (per-zone schedule + site arm mode).
* **Dwell** — how long they have been inside relative to the zone threshold.
* **Origin** — where the track first appeared: stepping out of a vehicle,
  through an entrance zone, at a perimeter edge, or mid-scene.
* **Behaviour** — live trajectory features (loitering, casing, passing
  through) from a sliding window.
* **Context** — time of day, group size, close contact between persons
  (pre-fight signal, based on metric ground distance), secondary crime
  classifier hits, blacklisted vehicles.
* **Authorization** — plate/manual/identity grants collapse the score.

Scores map onto an escalation ladder (observe → suspicious → alert →
critical). Only *alert*/*critical* transitions produce ``RiskEvent`` records
that downstream code persists as alerts; everything else stays visible in the
live feed without paging anyone.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import get_settings
from app.detection.geometry import CameraGeometry, ground_distance
from app.detection.roi import RoiZone
from app.services.authorization import AuthDecision, authorization_registry
from app.services.trajectory import TrajectoryPoint, compute_features

logger = logging.getLogger(__name__)

VEHICLE_CLASSES = frozenset({"car", "truck", "bus", "motorcycle"})
LEVELS = ("observe", "suspicious", "alert", "critical")

# Base risk for presence in an armed / disarmed zone by zone type. Tuned so
# that a plain unauthorized person in an armed restricted zone sits just
# below "alert" until the dwell threshold is crossed (+20), while aggravating
# context (night, perimeter origin, group) can push it over earlier.
ZONE_BASE_ARMED: dict[str, float] = {
    "restricted": 40.0,
    "perimeter": 35.0,
    "entrance": 10.0,
    "driveway": 10.0,
    "parking": 5.0,
    "public": 0.0,
}
ZONE_BASE_DISARMED: dict[str, float] = {
    "restricted": 10.0,
    "perimeter": 8.0,
    "entrance": 0.0,
    "driveway": 0.0,
    "parking": 0.0,
    "public": 0.0,
}

EDGE_MARGIN_RATIO = 0.08
WINDOW_SEC = 30.0
CRIME_MEMORY_SEC = 30.0


@dataclass
class RiskEvent:
    camera_id: str
    tenant_id: str
    object_id: int
    class_label: str
    score: float
    level: str
    previous_level: str
    reasons: list[str]
    timestamp_unix: float
    intrusion: bool
    incident_id: Optional[str]
    zone_ids: list[int]
    zone_names: list[str]
    distance_m: Optional[float]
    authorized: bool
    threat: bool

    def to_dict(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "object_id": self.object_id,
            "class_label": self.class_label,
            "score": round(self.score, 1),
            "level": self.level,
            "previous_level": self.previous_level,
            "reasons": list(self.reasons),
            "timestamp_unix": round(self.timestamp_unix, 3),
            "intrusion": self.intrusion,
            "incident_id": self.incident_id,
            "zone_ids": list(self.zone_ids),
            "zone_names": list(self.zone_names),
            "distance_m": self.distance_m,
            "authorized": self.authorized,
            "threat": self.threat,
        }


@dataclass
class _TrackContext:
    first_seen: float
    last_seen: float
    class_label: str
    origin_anchor: tuple[float, float]
    origin_kind: str = "interior"
    origin_zone_types: list[str] = field(default_factory=list)
    linked_vehicle_track: Optional[int] = None
    spawned_from_vehicle: bool = False
    points: deque = field(default_factory=deque)  # TrajectoryPoint window
    ground_points: deque = field(default_factory=deque)  # (t, x_m, y_m)
    zones_visited: set[int] = field(default_factory=set)
    close_contact_since: dict[int, float] = field(default_factory=dict)
    crime_confidence: float = 0.0
    crime_at: float = 0.0
    last_level: str = "observe"
    last_alert_level: str = "observe"
    last_alert_time: float = 0.0
    frame_counter: int = 0


class RiskEngine:
    """Per-camera online risk scorer."""

    def __init__(
        self,
        camera_id: str,
        tenant_id: str,
        geometry: Optional[CameraGeometry] = None,
        registry=None,
    ):
        settings = get_settings()
        self._enabled = bool(settings.RISK_ENGINE_ENABLED)
        self._camera_id = str(camera_id)
        self._tenant_id = str(tenant_id)
        self._geometry = geometry or CameraGeometry()
        self._registry = registry or authorization_registry
        self._settings = settings
        self._intruder_classes = {c.lower() for c in settings.ROI_INTRUDER_CLASSES}
        self._contexts: dict[int, _TrackContext] = {}
        self._alerted_incidents: dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_summary: dict = {"max_score": 0.0, "level": "observe", "persons": 0}

    # -- external signals ----------------------------------------------------

    @property
    def geometry(self) -> CameraGeometry:
        return self._geometry

    def set_geometry(self, geometry: CameraGeometry) -> None:
        self._geometry = geometry

    def mark_crime(self, object_id: int, confidence: float) -> None:
        with self._lock:
            ctx = self._contexts.get(int(object_id))
            if ctx is not None:
                ctx.crime_confidence = float(confidence)
                ctx.crime_at = time.time()

    def summary(self) -> dict:
        return dict(self._last_summary)

    def reset(self) -> None:
        with self._lock:
            self._contexts.clear()
            self._alerted_incidents.clear()

    # -- main entry ------------------------------------------------------------

    def evaluate(
        self,
        tracked_objects: list[dict],
        zones: list[RoiZone],
        intrusion_events: list,
        frame_shape: tuple,
        now: float,
        now_dt: Optional[datetime] = None,
    ) -> list[RiskEvent]:
        if not self._enabled:
            return []

        frame_h = int(frame_shape[0])
        frame_w = int(frame_shape[1])
        self._geometry.update_resolution(frame_w, frame_h)
        wall = now_dt or datetime.fromtimestamp(now, tz=timezone.utc)
        zone_by_id = {zone.zone_id: zone for zone in zones}
        arm_mode = self._registry.arm_mode(self._tenant_id)
        quiet = self._in_quiet_hours(wall)

        # Geometry for everything first (vehicles are needed for linking).
        for obj in tracked_objects:
            bbox = (
                float(obj.get("bbox_x", 0.0)),
                float(obj.get("bbox_y", 0.0)),
                float(obj.get("bbox_w", 0.0)),
                float(obj.get("bbox_h", 0.0)),
            )
            obj.update(self._geometry.locate(str(obj.get("class_label", "")), bbox))

        vehicles = [o for o in tracked_objects if str(o.get("class_label", "")).lower() in VEHICLE_CLASSES]
        persons = [o for o in tracked_objects if str(o.get("class_label", "")).lower() in self._intruder_classes]
        incident_by_track: dict[int, str] = {}
        for event in intrusion_events:
            incident_by_track[int(event.object_id)] = getattr(event, "incident_id", None) or ""

        events: list[RiskEvent] = []
        with self._lock:
            active_ids: set[int] = set()
            for obj in persons:
                track_id = int(obj.get("object_id", -1))
                if track_id < 0:
                    continue
                active_ids.add(track_id)
                ctx = self._contexts.get(track_id)
                if ctx is None:
                    ctx = self._spawn_context(obj, vehicles, zone_by_id, frame_w, frame_h, now)
                    self._contexts[track_id] = ctx
                elif ctx.linked_vehicle_track is None and now - ctx.first_seen <= 3.0:
                    # A person is often first seen half-hidden by the car door;
                    # keep trying to associate for a few seconds.
                    linked = self._find_vehicle(obj, vehicles)
                    if linked is not None:
                        ctx.linked_vehicle_track = int(linked.get("object_id", -1))
                        ctx.spawned_from_vehicle = True
                        ctx.origin_kind = "vehicle"
                self._update_context(ctx, obj, now)

            self._update_close_contacts(persons, now)

            for obj in persons:
                track_id = int(obj.get("object_id", -1))
                if track_id < 0:
                    continue
                ctx = self._contexts[track_id]
                event = self._score(
                    obj, ctx, zone_by_id, arm_mode, quiet, len(persons), now, wall, incident_by_track.get(track_id)
                )
                if event is not None:
                    events.append(event)

            self._expire(now, active_ids)
            self._last_summary = self._build_summary(persons)

        return events

    # -- context lifecycle -----------------------------------------------------

    def _spawn_context(
        self,
        obj: dict,
        vehicles: list[dict],
        zone_by_id: dict[int, RoiZone],
        frame_w: int,
        frame_h: int,
        now: float,
    ) -> _TrackContext:
        ax = float(obj.get("anchor_x", obj.get("bbox_x", 0.0) + obj.get("bbox_w", 0.0) / 2.0))
        ay = float(obj.get("anchor_y", obj.get("bbox_y", 0.0) + obj.get("bbox_h", 0.0)))
        ctx = _TrackContext(
            first_seen=now,
            last_seen=now,
            class_label=str(obj.get("class_label", "person")),
            origin_anchor=(ax, ay),
        )

        linked = self._find_vehicle(obj, vehicles)
        if linked is not None:
            ctx.linked_vehicle_track = int(linked.get("object_id", -1))
            ctx.spawned_from_vehicle = True
            ctx.origin_kind = "vehicle"
        else:
            zone_types = [
                zone_by_id[zid].zone_type for zid in obj.get("roi_zone_ids", []) if zid in zone_by_id
            ]
            ctx.origin_zone_types = zone_types
            if "entrance" in zone_types:
                ctx.origin_kind = "entrance"
            elif "perimeter" in zone_types:
                ctx.origin_kind = "perimeter"
            else:
                ctx.origin_kind = self._edge_kind(ax, ay, frame_w, frame_h)
        return ctx

    @staticmethod
    def _edge_kind(x: float, y: float, frame_w: int, frame_h: int) -> str:
        mx = frame_w * EDGE_MARGIN_RATIO
        my = frame_h * EDGE_MARGIN_RATIO
        if x <= mx:
            return "edge_left"
        if x >= frame_w - mx:
            return "edge_right"
        if y <= my:
            return "edge_top"
        if y >= frame_h - my:
            return "edge_bottom"
        return "interior"

    @staticmethod
    def _find_vehicle(person: dict, vehicles: list[dict]) -> Optional[dict]:
        ax = float(person.get("anchor_x", 0.0))
        ay = float(person.get("anchor_y", 0.0))
        best = None
        best_area = float("inf")
        for vehicle in vehicles:
            vx = float(vehicle.get("bbox_x", 0.0))
            vy = float(vehicle.get("bbox_y", 0.0))
            vw = float(vehicle.get("bbox_w", 0.0))
            vh = float(vehicle.get("bbox_h", 0.0))
            pad_x = vw * 0.25
            pad_y = vh * 0.25
            if (vx - pad_x) <= ax <= (vx + vw + pad_x) and (vy - pad_y) <= ay <= (vy + vh + pad_y):
                area = vw * vh
                if area < best_area:
                    best = vehicle
                    best_area = area
        return best

    def _update_context(self, ctx: _TrackContext, obj: dict, now: float) -> None:
        ctx.last_seen = now
        ctx.frame_counter += 1
        ctx.zones_visited.update(int(z) for z in obj.get("roi_zone_ids", []))
        point = TrajectoryPoint(
            x=float(obj.get("anchor_x", 0.0)),
            y=float(obj.get("anchor_y", 0.0)),
            w=float(obj.get("bbox_w", 0.0)),
            h=float(obj.get("bbox_h", 0.0)),
            confidence=float(obj.get("confidence", 0.0)),
            frame_number=int(obj.get("frame_number", 0)),
            timestamp=now,
            roi_zone_ids=[int(z) for z in obj.get("roi_zone_ids", [])],
            has_intrusion=bool(obj.get("intrusion", False)),
        )
        ctx.points.append(point)
        while ctx.points and now - ctx.points[0].timestamp > WINDOW_SEC:
            ctx.points.popleft()

        gx, gy = obj.get("ground_x_m"), obj.get("ground_y_m")
        if gx is not None and gy is not None:
            ctx.ground_points.append((now, float(gx), float(gy)))
            while ctx.ground_points and now - ctx.ground_points[0][0] > 5.0:
                ctx.ground_points.popleft()

    def _update_close_contacts(self, persons: list[dict], now: float) -> None:
        threshold = float(self._settings.RISK_CLOSE_CONTACT_M)
        ids = [int(p.get("object_id", -1)) for p in persons]
        for i, a in enumerate(persons):
            ctx_a = self._contexts.get(ids[i])
            if ctx_a is None:
                continue
            current: set[int] = set()
            for j, b in enumerate(persons):
                if i == j:
                    continue
                dist = ground_distance(a, b)
                if dist is None:
                    dist = self._pixel_proximity(a, b)
                if dist is not None and dist <= threshold:
                    current.add(ids[j])
                    ctx_a.close_contact_since.setdefault(ids[j], now)
            for other in list(ctx_a.close_contact_since.keys()):
                if other not in current:
                    del ctx_a.close_contact_since[other]

    @staticmethod
    def _pixel_proximity(a: dict, b: dict) -> Optional[float]:
        """Fallback when no metric ground data: scale pixel gap by person height."""
        ha = float(a.get("bbox_h", 0.0))
        hb = float(b.get("bbox_h", 0.0))
        if ha <= 1 or hb <= 1:
            return None
        ax, ay = float(a.get("anchor_x", 0.0)), float(a.get("anchor_y", 0.0))
        bx, by = float(b.get("anchor_x", 0.0)), float(b.get("anchor_y", 0.0))
        px = math.hypot(ax - bx, ay - by)
        metres_per_px = 1.7 / ((ha + hb) / 2.0)
        return px * metres_per_px

    def _expire(self, now: float, active_ids: set[int]) -> None:
        grace = float(self._settings.ROI_TRACK_GRACE_SEC) + 3.0
        for track_id in list(self._contexts.keys()):
            if track_id in active_ids:
                continue
            if now - self._contexts[track_id].last_seen > grace:
                del self._contexts[track_id]
        for incident_id in [k for k, ts in self._alerted_incidents.items() if now - ts > 600]:
            del self._alerted_incidents[incident_id]

    # -- scoring -----------------------------------------------------------------

    def _score(
        self,
        obj: dict,
        ctx: _TrackContext,
        zone_by_id: dict[int, RoiZone],
        arm_mode: str,
        quiet: bool,
        person_count: int,
        now: float,
        wall: datetime,
        incident_id: Optional[str],
    ) -> Optional[RiskEvent]:
        settings = self._settings
        reasons: list[str] = []
        score = 0.0
        track_id = int(obj.get("object_id", -1))

        zone_ids = [int(z) for z in obj.get("roi_zone_ids", [])]
        zones_here = [zone_by_id[z] for z in zone_ids if z in zone_by_id]
        armed_here: list[RoiZone] = []
        for zone in zones_here:
            armed = self._zone_armed(zone, arm_mode, wall)
            if str(obj.get("class_label", "")).lower() in zone.allowed_classes:
                continue
            base = (ZONE_BASE_ARMED if armed else ZONE_BASE_DISARMED).get(zone.zone_type, 0.0)
            if base > 0:
                score = max(score, base)
                reasons.append(
                    f"in {'armed' if armed else 'disarmed'} {zone.zone_type} zone '{zone.name}'"
                )
            if armed:
                armed_here.append(zone)

        dwell = float(obj.get("max_roi_dwell_sec", 0.0))
        if armed_here and dwell > 0:
            threshold = max(0.5, min(z.threshold_sec for z in armed_here))
            if dwell >= threshold:
                # Crossing the zone threshold is the decisive step.
                score += 20.0 + min(10.0, 10.0 * (dwell - threshold) / max(threshold, 1.0) / 3.0)
                reasons.append(f"dwelling {dwell:.0f}s (threshold {threshold:.0f}s)")
            else:
                score += 5.0 * dwell / threshold

        # Origin
        if ctx.origin_kind == "vehicle":
            score -= 15.0
            reasons.append("arrived by vehicle")
        elif ctx.origin_kind == "entrance":
            score -= 20.0
            reasons.append("entered through entrance zone")
        elif ctx.origin_kind == "perimeter":
            score += 15.0
            reasons.append("appeared at perimeter")

        # Behaviour from live trajectory window
        behaviour = self._behaviour(ctx)
        if behaviour == "loitering":
            score += 15.0
            reasons.append("loitering")
        elif behaviour == "casing":
            score += 10.0
            reasons.append("erratic movement / casing")
        elif behaviour == "passing_through" and not armed_here:
            score -= 10.0

        # Context
        if quiet:
            score += 10.0
            reasons.append("quiet hours")
        if person_count >= 3:
            score += 10.0
            reasons.append(f"group of {person_count}")
        contacts = [
            other
            for other, since in ctx.close_contact_since.items()
            if now - since >= float(settings.RISK_CLOSE_CONTACT_SEC)
        ]
        if contacts:
            score += 15.0 if len(contacts) >= 2 else 10.0
            reasons.append(f"close contact with #{', #'.join(str(c) for c in contacts)}")
        if ctx.crime_confidence > 0 and now - ctx.crime_at <= CRIME_MEMORY_SEC:
            score += 30.0 * ctx.crime_confidence
            reasons.append(f"crime classifier {ctx.crime_confidence:.0%}")

        # Authorization
        decision: AuthDecision = self._registry.resolve(
            self._tenant_id,
            self._camera_id,
            track_id,
            linked_vehicle_track=ctx.linked_vehicle_track,
            spawned_from_vehicle=ctx.spawned_from_vehicle,
        )
        if decision.threat:
            score += 40.0
            reasons.append(f"linked to {decision.label}")
        elif decision.authorized:
            score *= 0.2
            reasons.append(f"authorized ({decision.label})")

        score = max(0.0, min(100.0, score))
        level = self._level(score)

        obj["risk_score"] = round(score, 1)
        obj["risk_level"] = level
        obj["risk_reasons"] = reasons
        obj["authorized"] = bool(decision.authorized)
        obj["auth_source"] = decision.source
        obj["auth_label"] = decision.label
        obj["threat"] = bool(decision.threat)
        obj["origin"] = ctx.origin_kind
        obj["linked_vehicle_track"] = ctx.linked_vehicle_track
        obj["behaviour"] = behaviour
        obj["close_contacts"] = sorted(ctx.close_contact_since.keys())
        obj["speed_mps"] = self._ground_speed(ctx)
        obj["track_age_sec"] = round(now - ctx.first_seen, 1)

        event: Optional[RiskEvent] = None
        intrusion = bool(obj.get("intrusion", False)) and not decision.authorized
        if level in ("alert", "critical"):
            level_rank = LEVELS.index(level)
            prev_rank = LEVELS.index(ctx.last_alert_level)
            cooled = now - ctx.last_alert_time >= float(settings.RISK_ALERT_COOLDOWN_SEC)
            incident_fresh = bool(incident_id) and incident_id not in self._alerted_incidents
            # Emit when the track escalates to a higher level (a fresh climb
            # from "observe" additionally respects the cooldown so a
            # flickering score cannot double-fire), or when the zone
            # threshold is crossed for a not-yet-reported incident.
            escalated = level_rank > prev_rank and (prev_rank > 0 or ctx.last_alert_time == 0.0 or cooled)
            if escalated or (intrusion and incident_fresh):
                event = RiskEvent(
                    camera_id=self._camera_id,
                    tenant_id=self._tenant_id,
                    object_id=track_id,
                    class_label=str(obj.get("class_label", "person")),
                    score=score,
                    level=level,
                    previous_level=ctx.last_level,
                    reasons=list(reasons),
                    timestamp_unix=now,
                    intrusion=intrusion,
                    incident_id=incident_id or None,
                    zone_ids=zone_ids,
                    zone_names=[z.name for z in zones_here],
                    distance_m=obj.get("distance_m"),
                    authorized=bool(decision.authorized),
                    threat=bool(decision.threat),
                )
                ctx.last_alert_level = level
                ctx.last_alert_time = now
                if incident_id:
                    self._alerted_incidents[incident_id] = now
        elif level == "observe" and ctx.last_alert_level != "observe":
            # Allow re-alerting when the same track escalates again later.
            ctx.last_alert_level = "observe"
        ctx.last_level = level
        return event

    def _zone_armed(self, zone: RoiZone, arm_mode: str, wall: datetime) -> bool:
        if arm_mode == "armed":
            return True
        if arm_mode == "disarmed":
            return False
        return zone.is_armed(wall)

    def _level(self, score: float) -> str:
        s = self._settings
        if score >= s.RISK_LEVEL_CRITICAL:
            return "critical"
        if score >= s.RISK_LEVEL_ALERT:
            return "alert"
        if score >= s.RISK_LEVEL_SUSPICIOUS:
            return "suspicious"
        return "observe"

    @staticmethod
    def _behaviour(ctx: _TrackContext) -> str:
        if len(ctx.points) < 5:
            return "unknown"
        features = compute_features(0, ctx.class_label, list(ctx.points))
        if features is None or features.duration_sec < 3.0:
            return "unknown"
        dir_rate = features.direction_changes / features.duration_sec if features.duration_sec > 0 else 0.0
        if features.duration_sec >= 12 and features.stationary_ratio >= 0.5 and features.bbox_coverage < 0.15:
            return "loitering"
        if features.duration_sec >= 8 and dir_rate >= 0.3 and features.avg_speed > 2.0:
            return "casing"
        if features.bbox_coverage >= 0.10 and features.direction_changes <= 3 and features.avg_speed > 2.0:
            return "passing_through"
        return "unknown"

    @staticmethod
    def _ground_speed(ctx: _TrackContext) -> Optional[float]:
        if len(ctx.ground_points) < 2:
            return None
        t0, x0, y0 = ctx.ground_points[0]
        t1, x1, y1 = ctx.ground_points[-1]
        dt = t1 - t0
        if dt <= 0.2:
            return None
        return round(math.hypot(x1 - x0, y1 - y0) / dt, 2)

    def _in_quiet_hours(self, wall: datetime) -> bool:
        s = self._settings
        try:
            tz = ZoneInfo(s.RISK_QUIET_HOURS_TZ)
        except (ZoneInfoNotFoundError, ValueError):
            tz = timezone.utc
        hour = wall.astimezone(tz).hour
        start, end = int(s.RISK_QUIET_HOURS_START), int(s.RISK_QUIET_HOURS_END)
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def _build_summary(self, persons: list[dict]) -> dict:
        max_score = 0.0
        level = "observe"
        authorized = 0
        for obj in persons:
            score = float(obj.get("risk_score", 0.0))
            if score > max_score:
                max_score = score
                level = str(obj.get("risk_level", "observe"))
            if obj.get("authorized"):
                authorized += 1
        return {
            "max_score": round(max_score, 1),
            "level": level,
            "persons": len(persons),
            "authorized": authorized,
        }
