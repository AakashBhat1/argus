"""ROI intrusion filter: foot anchor, dropout grace, incidents, arming, allowed classes."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pytest

from app.detection.roi import (
    ArmedSchedule,
    RoiIntrusionFilter,
    RoiZone,
    RoiZoneRepository,
    anchor_point,
)

FRAME = (540, 960, 3)
CAMERA = "cam-1"


class _StaticRepo:
    def __init__(self, zones):
        self._zones = zones

    def zones_for_camera(self, camera_id):
        return [z for z in self._zones if z.applies_to_camera(camera_id)]

    def all_zones(self):
        return list(self._zones)


def _zone(**overrides) -> RoiZone:
    base = dict(
        zone_id=1,
        name="yard",
        normalized_points=np.array([[0.25, 0.25], [0.75, 0.25], [0.75, 0.80], [0.25, 0.80]], dtype=np.float32),
        threshold_sec=2.0,
        color=(0, 255, 255),
        camera_ids=None,
    )
    base.update(overrides)
    return RoiZone(**base)


def _person(track_id: int, x: float, y: float, w: float = 60, h: float = 160, label: str = "person") -> dict:
    """bbox positioned so that its *foot point* is at (x, y)."""
    return {
        "object_id": track_id,
        "class_label": label,
        "bbox_x": x - w / 2,
        "bbox_y": y - h,
        "bbox_w": w,
        "bbox_h": h,
        "confidence": 0.9,
    }


def _filter(zones, **kwargs) -> RoiIntrusionFilter:
    return RoiIntrusionFilter(
        camera_id=CAMERA,
        zone_repository=_StaticRepo(zones),
        intruder_classes=["person"],
        alert_cooldown_sec=kwargs.pop("cooldown", 10.0),
        track_grace_sec=kwargs.pop("grace", 2.0),
        incident_holddown_sec=kwargs.pop("holddown", 30.0),
        anchor=kwargs.pop("anchor", "foot"),
    )


def test_anchor_point_uses_bottom_centre():
    obj = {"bbox_x": 100, "bbox_y": 50, "bbox_w": 40, "bbox_h": 160}
    assert anchor_point(obj, "foot") == (120.0, 210.0)
    assert anchor_point(obj, "center") == (120.0, 130.0)


def test_anchor_point_falls_back_to_centre_when_feet_are_cut_off():
    """Person too close to the camera: bbox touches the bottom edge, feet unseen."""
    obj = {"bbox_x": 100, "bbox_y": 200, "bbox_w": 200, "bbox_h": 520}
    assert anchor_point(obj, "foot", (720, 1280)) == (200.0, 460.0)
    # Not truncated: normal foot point.
    obj_ok = {"bbox_x": 100, "bbox_y": 200, "bbox_w": 200, "bbox_h": 400}
    assert anchor_point(obj_ok, "foot", (720, 1280)) == (200.0, 600.0)


def test_truncated_person_still_counts_inside_a_floor_zone():
    zone = _zone(
        normalized_points=np.array([[0.05, 0.05], [0.95, 0.05], [0.95, 0.98], [0.05, 0.98]], dtype=np.float32),
        threshold_sec=1.0,
    )
    flt = _filter([zone])
    obj = {"object_id": 1, "class_label": "person", "bbox_x": 400, "bbox_y": 150, "bbox_w": 300, "bbox_h": 570}
    flt.evaluate([obj], (720, 1280), 100.0, arm_mode="armed")
    assert obj["inside_roi"] is True


def test_foot_point_outside_zone_does_not_count_even_if_center_inside():
    """Standing just below a floor polygon: centre is inside, feet are not."""
    zone = _zone()
    flt = _filter([zone])
    # zone bottom edge at y = 0.80 * 540 = 432. Foot at 440 (outside), centre at 360 (inside).
    obj = _person(1, x=480, y=440)
    flt.evaluate([obj], FRAME, timestamp=0.0)
    assert obj["inside_roi"] is False

    center_filter = _filter([zone], anchor="center")
    obj2 = _person(1, x=480, y=440)
    center_filter.evaluate([obj2], FRAME, timestamp=0.0)
    assert obj2["inside_roi"] is True


def test_dwell_threshold_emits_single_new_incident():
    flt = _filter([_zone(threshold_sec=2.0)])
    events = []
    for t in (0.0, 1.0, 2.0, 2.5):
        events += flt.evaluate([_person(7, 480, 300)], FRAME, timestamp=t)
    assert len(events) == 1
    assert events[0].new_incident is True
    assert events[0].dwell_seconds >= 2.0
    assert events[0].incident_id


def test_dwell_survives_short_tracking_dropout():
    flt = _filter([_zone(threshold_sec=3.0)], grace=2.0)
    flt.evaluate([_person(1, 480, 300)], FRAME, timestamp=0.0)
    flt.evaluate([_person(1, 480, 300)], FRAME, timestamp=1.0)
    # dropout for 1.5s (< grace)
    flt.evaluate([], FRAME, timestamp=1.5)
    flt.evaluate([], FRAME, timestamp=2.5)
    events = flt.evaluate([_person(1, 480, 300)], FRAME, timestamp=3.1)
    assert len(events) == 1, "dwell clock must not restart after a short dropout"


def test_dwell_resets_after_long_dropout():
    flt = _filter([_zone(threshold_sec=3.0)], grace=2.0)
    flt.evaluate([_person(1, 480, 300)], FRAME, timestamp=0.0)
    flt.evaluate([], FRAME, timestamp=1.0)
    flt.evaluate([], FRAME, timestamp=4.0)  # > grace
    events = flt.evaluate([_person(1, 480, 300)], FRAME, timestamp=4.5)
    assert events == []


def test_reidentified_track_joins_existing_incident():
    flt = _filter([_zone(threshold_sec=1.0)])
    first = []
    for t in (0.0, 1.0):
        first += flt.evaluate([_person(1, 480, 300)], FRAME, timestamp=t)
    assert len(first) == 1 and first[0].new_incident

    # Tracker switches ID 1 -> 2 for the same person.
    second = []
    for t in (1.2, 2.2):
        second += flt.evaluate([_person(2, 480, 300)], FRAME, timestamp=t)
    assert len(second) == 1
    assert second[0].new_incident is False
    assert second[0].incident_id == first[0].incident_id
    assert second[0].intruder_count == 2


def test_incident_closes_after_holddown():
    flt = _filter([_zone(threshold_sec=1.0)], holddown=5.0)
    for t in (0.0, 1.0):
        flt.evaluate([_person(1, 480, 300)], FRAME, timestamp=t)
    assert flt.active_incidents()
    flt.evaluate([], FRAME, timestamp=7.0)
    assert flt.active_incidents() == {}
    events = []
    for t in (8.0, 9.0):
        events += flt.evaluate([_person(3, 480, 300)], FRAME, timestamp=t)
    assert len(events) == 1 and events[0].new_incident is True


def test_allowed_class_never_intrudes():
    zone = _zone(threshold_sec=0.5, allowed_classes={"person"})
    flt = _filter([zone])
    events = []
    for t in (0.0, 1.0, 2.0):
        obj = _person(1, 480, 300)
        events += flt.evaluate([obj], FRAME, timestamp=t)
    assert events == []
    assert obj["inside_roi"] is True  # presence is still reported


def test_disarmed_zone_reports_presence_but_no_intrusion():
    zone = _zone(threshold_sec=0.5, armed_schedule=ArmedSchedule(mode="never"))
    flt = _filter([zone])
    events = []
    for t in (0.0, 1.0):
        obj = _person(1, 480, 300)
        events += flt.evaluate([obj], FRAME, timestamp=t)
    assert events == []
    assert obj["inside_roi"] is True
    assert obj["roi_armed_zone_ids"] == []
    assert obj["max_roi_dwell_sec"] >= 1.0


def test_site_arm_override_forces_armed():
    zone = _zone(threshold_sec=0.5, armed_schedule=ArmedSchedule(mode="never"))
    flt = _filter([zone])
    events = []
    for t in (0.0, 1.0):
        events += flt.evaluate([_person(1, 480, 300)], FRAME, timestamp=t, arm_mode="armed")
    assert len(events) == 1


def test_site_disarm_override_suppresses_armed_zone():
    flt = _filter([_zone(threshold_sec=0.5)])
    events = []
    for t in (0.0, 1.0):
        events += flt.evaluate([_person(1, 480, 300)], FRAME, timestamp=t, arm_mode="disarmed")
    assert events == []


@pytest.mark.parametrize(
    "hour,expected",
    [(23, True), (3, True), (12, False), (6, False), (21, False)],
)
def test_overnight_schedule(hour, expected):
    schedule = ArmedSchedule(mode="schedule", windows=[{"start": "22:00", "end": "06:00", "days": list(range(7))}])
    now = datetime(2026, 9, 4, hour, 30, tzinfo=timezone.utc)
    assert schedule.is_armed(now) is expected


def test_schedule_respects_days():
    # Only armed on Monday (0).
    schedule = ArmedSchedule(mode="schedule", windows=[{"start": "09:00", "end": "17:00", "days": [0]}])
    monday = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    tuesday = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    assert schedule.is_armed(monday) is True
    assert schedule.is_armed(tuesday) is False


def test_repository_parses_semantics(tmp_path):
    cfg = tmp_path / "zones.json"
    cfg.write_text(
        json.dumps(
            [
                {
                    "zone_id": 5,
                    "name": "gate",
                    "points": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]],
                    "zone_type": "entrance",
                    "armed_schedule": {"mode": "schedule", "windows": [{"start": "22:00", "end": "06:00"}]},
                    "allowed_classes": ["Car", "truck"],
                },
                {"zone_id": 6, "name": "legacy", "points": [[100, 100], [400, 100], [400, 300]]},
            ]
        )
    )
    repo = RoiZoneRepository(config_path=str(cfg))
    zones = {z.zone_id: z for z in repo.all_zones()}
    assert zones[5].zone_type == "entrance"
    assert zones[5].armed_schedule.mode == "schedule"
    assert zones[5].allowed_classes == {"car", "truck"}
    # Legacy pixel-based zones default to restricted / always armed.
    assert zones[6].zone_type == "restricted"
    assert zones[6].is_armed() is True
    assert float(zones[6].normalized_points.max()) <= 1.0
