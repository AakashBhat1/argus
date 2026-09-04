"""Contextual risk engine + authorization registry."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from app.detection.geometry import CameraCalibration, CameraGeometry
from app.detection.roi import ArmedSchedule, RoiIntrusionFilter, RoiZone
from app.services.authorization import AuthorizationRegistry
from app.services.risk_engine import RiskEngine

FRAME = (540, 960, 3)
CAMERA = "cam-risk"
TENANT = "tenant-risk"
NOON = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
MIDNIGHT = datetime(2026, 9, 4, 0, 30, tzinfo=timezone.utc)


class _StaticRepo:
    def __init__(self, zones):
        self._zones = zones

    def zones_for_camera(self, camera_id):
        return list(self._zones)

    def all_zones(self):
        return list(self._zones)


def _zone(zone_type="restricted", armed=True, zone_id=1, points=None, threshold=2.0, allowed=None):
    return RoiZone(
        zone_id=zone_id,
        name=f"{zone_type}-{zone_id}",
        normalized_points=np.array(
            points or [[0.25, 0.25], [0.75, 0.25], [0.75, 0.80], [0.25, 0.80]], dtype=np.float32
        ),
        threshold_sec=threshold,
        color=(0, 255, 255),
        zone_type=zone_type,
        armed_schedule=ArmedSchedule(mode="always" if armed else "never"),
        allowed_classes=set(allowed or []),
    )


def _person(track_id, x, y, w=60, h=160, label="person"):
    return {
        "object_id": track_id,
        "class_label": label,
        "bbox_x": x - w / 2,
        "bbox_y": y - h,
        "bbox_w": w,
        "bbox_h": h,
        "confidence": 0.9,
        "frame_number": 0,
    }


def _car(track_id, x, y, w=220, h=120):
    return {
        "object_id": track_id,
        "class_label": "car",
        "bbox_x": x - w / 2,
        "bbox_y": y - h,
        "bbox_w": w,
        "bbox_h": h,
        "confidence": 0.9,
        "frame_number": 0,
    }


class Harness:
    def __init__(self, zones, registry=None):
        self.registry = registry or AuthorizationRegistry()
        self.roi = RoiIntrusionFilter(
            camera_id=CAMERA,
            zone_repository=_StaticRepo(zones),
            intruder_classes=["person"],
            alert_cooldown_sec=10.0,
            track_grace_sec=2.0,
            incident_holddown_sec=30.0,
            anchor="foot",
        )
        self.engine = RiskEngine(
            camera_id=CAMERA,
            tenant_id=TENANT,
            geometry=CameraGeometry(960, 540, CameraCalibration(hfov_deg=90.0)),
            registry=self.registry,
        )

    def step(self, objects, t, wall=NOON):
        arm_mode = self.registry.arm_mode(TENANT)
        intrusions = self.roi.evaluate(objects, FRAME, timestamp=t, now_dt=wall, arm_mode=arm_mode)
        events = self.engine.evaluate(objects, self.roi.zones(), intrusions, FRAME, now=t, now_dt=wall)
        return objects, intrusions, events

    def run(self, make_objects, times, wall=NOON):
        all_events = []
        last = None
        for t in times:
            objs = make_objects(t)
            last, _, events = self.step(objs, t, wall)
            all_events += events
        return last, all_events


def test_unauthorized_person_in_armed_restricted_zone_escalates():
    h = Harness([_zone("restricted")])
    objs, events = h.run(lambda t: [_person(1, 480, 300)], [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
    person = objs[0]
    assert person["risk_level"] in ("alert", "critical")
    assert any("armed restricted" in r for r in person["risk_reasons"])
    assert person["distance_m"] is not None and person["distance_m"] > 0
    assert len(events) >= 1
    assert events[0].intrusion is True
    assert events[0].level in ("alert", "critical")


def test_alert_fires_at_threshold_not_before():
    h = Harness([_zone("restricted", threshold=2.0)])
    _, events = h.run(lambda t: [_person(1, 480, 300)], [0.0, 0.5, 1.0, 1.5])
    assert events == [], "plain presence below the dwell threshold is suspicious, not an alert"
    _, events = h.run(lambda t: [_person(1, 480, 300)], [2.0, 2.5])
    assert len(events) == 1 and events[0].intrusion is True and events[0].level == "alert"


def test_same_incident_only_reports_escalations():
    h = Harness([_zone("restricted")])
    _, events = h.run(lambda t: [_person(1, 480, 300)], [x * 0.5 for x in range(0, 40)])
    # One alert at the threshold crossing, one escalation to critical once
    # the person is clearly loitering. No periodic re-alerts in between.
    assert [e.level for e in events] == ["alert", "critical"]
    assert all(e.intrusion for e in events)
    assert len({e.incident_id for e in events}) == 1


def test_authorized_visitor_window_collapses_risk():
    h = Harness([_zone("restricted")])
    h.registry.grant_site(TENANT, minutes=10, label="Plumber expected")
    objs, events = h.run(lambda t: [_person(1, 480, 300)], [0.0, 1.0, 2.0, 3.0, 4.0])
    person = objs[0]
    assert person["authorized"] is True
    assert person["auth_label"] == "Plumber expected"
    assert person["risk_level"] == "observe"
    assert events == []


def test_person_stepping_out_of_authorized_vehicle_is_authorized():
    h = Harness([_zone("driveway", allowed=["car"])])
    # Gate OCR (on another camera) read a resident plate on vehicle track 42.
    h.registry.register_vehicle_plate(TENANT, camera_id="gate-cam", track_id=42, plate_text="KA01AB1234", profile_type="resident")

    car = _car(9, 480, 420)

    def objects(t):
        if t < 1.0:
            return [car]
        return [car, _person(3, 480 + 90, 420)]  # appears next to the car

    objs, events = h.run(objects, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    person = [o for o in objs if o["class_label"] == "person"][0]
    assert person["origin"] == "vehicle"
    assert person["linked_vehicle_track"] == 9
    assert person["authorized"] is True
    assert person["auth_source"] == "plate_site"
    assert events == []


def test_person_from_blacklisted_vehicle_is_a_threat():
    h = Harness([_zone("driveway", allowed=["car"])])
    h.registry.register_vehicle_plate(TENANT, camera_id=CAMERA, track_id=9, plate_text="MH12ZZ0001", profile_type="blacklist")
    car = _car(9, 480, 420)
    objs, events = h.run(lambda t: [car] if t < 1 else [car, _person(3, 570, 420)], [0.0, 1.0, 2.0, 3.0])
    person = [o for o in objs if o["class_label"] == "person"][0]
    assert person["threat"] is True
    assert person["risk_level"] in ("alert", "critical")
    assert events and events[0].threat is True


def test_walking_across_disarmed_driveway_is_observe():
    h = Harness([_zone("driveway", armed=False, threshold=1.0)])
    # walk left -> right across the frame at daytime
    objs, events = h.run(lambda t: [_person(1, 100 + t * 150, 300)], [x * 0.5 for x in range(0, 11)])
    assert objs[0]["risk_level"] == "observe"
    assert events == []


def test_site_disarm_suppresses_restricted_zone_alerts():
    h = Harness([_zone("restricted")])
    h.registry.set_arm_mode(TENANT, "disarmed")
    objs, events = h.run(lambda t: [_person(1, 480, 300)], [0.0, 1.0, 2.0, 3.0, 4.0])
    assert objs[0]["risk_level"] in ("observe", "suspicious")
    assert events == []


def test_quiet_hours_and_group_size_raise_score():
    h_day = Harness([_zone("perimeter")])
    day_objs, _ = h_day.run(lambda t: [_person(1, 480, 300)], [0.0, 1.0, 2.0], wall=NOON)
    h_night = Harness([_zone("perimeter")])
    night_objs, _ = h_night.run(
        lambda t: [_person(1, 480, 300), _person(2, 300, 300), _person(3, 700, 300)],
        [0.0, 1.0, 2.0],
        wall=MIDNIGHT,
    )
    assert night_objs[0]["risk_score"] > day_objs[0]["risk_score"]
    assert any("quiet hours" in r for r in night_objs[0]["risk_reasons"])
    assert any("group of 3" in r for r in night_objs[0]["risk_reasons"])


def test_close_contact_detected_from_metric_distance():
    h = Harness([_zone("public", armed=False)])
    # two people ~0.5 m apart for 3 s (pixel fallback: 1.7 m / 160 px)
    objs, _ = h.run(lambda t: [_person(1, 480, 300), _person(2, 520, 300)], [0.0, 1.0, 2.0, 3.0])
    assert 2 in objs[0]["close_contacts"]
    assert any("close contact" in r for r in objs[0]["risk_reasons"])


def test_crime_signal_feeds_back_into_score():
    h = Harness([_zone("public", armed=False)])
    h.run(lambda t: [_person(1, 480, 300)], [0.0, 1.0])
    baseline = h.step([_person(1, 480, 300)], 2.0)[0][0]["risk_score"]
    h.engine.mark_crime(1, 0.9)
    boosted = h.step([_person(1, 480, 300)], 3.0)[0][0]["risk_score"]
    assert boosted > baseline
    assert boosted - baseline == pytest.approx(27.0, abs=0.5)


def test_manual_track_grant_targets_one_track_only():
    h = Harness([_zone("restricted")])
    h.registry.grant_track(TENANT, camera_id=CAMERA, track_id=1, label="Guard on duty")
    objs, _ = h.run(lambda t: [_person(1, 480, 300), _person(2, 400, 300)], [0.0, 1.0, 2.0, 3.0])
    by_id = {o["object_id"]: o for o in objs}
    assert by_id[1]["authorized"] is True
    assert by_id[2]["authorized"] is False
    assert by_id[2]["risk_score"] > by_id[1]["risk_score"]


def test_registry_grant_expiry_and_revoke(monkeypatch):
    reg = AuthorizationRegistry()
    grant = reg.grant_site(TENANT, minutes=1, label="x")
    assert reg.resolve(TENANT, CAMERA, 1).authorized
    assert reg.revoke(TENANT, grant.grant_id) is True
    assert reg.resolve(TENANT, CAMERA, 1).authorized is False

    reg.grant_site(TENANT, minutes=1, label="y")
    import app.services.authorization as auth_mod

    real_time = auth_mod.time.time
    monkeypatch.setattr(auth_mod.time, "time", lambda: real_time() + 120)
    assert reg.resolve(TENANT, CAMERA, 1).authorized is False
    assert reg.list_grants(TENANT) == []


def test_arm_mode_validation():
    reg = AuthorizationRegistry()
    assert reg.arm_mode(TENANT) == "auto"
    assert reg.set_arm_mode(TENANT, "armed") == "armed"
    with pytest.raises(ValueError):
        reg.set_arm_mode(TENANT, "panic")
