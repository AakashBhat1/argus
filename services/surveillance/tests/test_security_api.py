"""Security console API, zone semantics API and camera calibration persistence."""

from __future__ import annotations

import json

import pytest

from app.services.authorization import authorization_registry


@pytest.fixture(autouse=True)
def _isolated_zone_config(tmp_path, monkeypatch):
    """Point the zones router at a temp file so tests never touch the repo config."""
    from app.config import get_settings

    settings = get_settings()
    cfg = tmp_path / "zones.json"
    monkeypatch.setattr(settings, "ROI_ZONES_CONFIG_PATH", str(cfg))
    yield cfg


@pytest.mark.asyncio
async def test_security_state_and_arm_toggle(auth_client, admin_user):
    res = await auth_client.get("/api/v1/security/state")
    assert res.status_code == 200
    body = res.json()
    assert body["arm_mode"] in ("auto", "armed", "disarmed")
    assert "grants" in body and "cameras" in body

    res = await auth_client.put("/api/v1/security/arm", json={"mode": "armed"})
    assert res.status_code == 200
    assert res.json()["arm_mode"] == "armed"
    assert authorization_registry.arm_mode(admin_user.tenant_id) == "armed"

    res = await auth_client.put("/api/v1/security/arm", json={"mode": "panic"})
    assert res.status_code == 422

    await auth_client.put("/api/v1/security/arm", json={"mode": "auto"})


@pytest.mark.asyncio
async def test_site_and_track_grants_lifecycle(auth_client, admin_user):
    res = await auth_client.post(
        "/api/v1/security/grants/site",
        json={"minutes": 5, "label": "Courier expected", "note": "DHL"},
    )
    assert res.status_code == 201
    grant = res.json()
    assert grant["scope"] == "site" and grant["kind"] == "manual"
    assert grant["seconds_remaining"] > 0

    res = await auth_client.post(
        "/api/v1/security/grants/track",
        json={"camera_id": "cam-x", "track_id": 12, "label": "Guard"},
    )
    assert res.status_code == 201
    track_grant = res.json()
    assert track_grant["scope"] == "track" and track_grant["track_id"] == 12

    res = await auth_client.get("/api/v1/security/grants")
    ids = {g["grant_id"] for g in res.json()}
    assert {grant["grant_id"], track_grant["grant_id"]} <= ids

    decision = authorization_registry.resolve(admin_user.tenant_id, "cam-x", 12)
    assert decision.authorized and decision.source == "track"

    res = await auth_client.delete(f"/api/v1/security/grants/{grant['grant_id']}")
    assert res.status_code == 204
    res = await auth_client.delete(f"/api/v1/security/grants/{grant['grant_id']}")
    assert res.status_code == 404
    await auth_client.delete(f"/api/v1/security/grants/{track_grant['grant_id']}")


@pytest.mark.asyncio
async def test_vehicle_registration_creates_plate_grant(auth_client, admin_user):
    res = await auth_client.post(
        "/api/v1/security/vehicles/register",
        json={"camera_id": "gate", "track_id": 3, "plate_text": "ka01ab9999", "profile_type": "resident", "owner_name": "Asha"},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["plate_text"] == "KA01AB9999"
    assert body["grant"]["kind"] == "plate"
    assert "Asha" in body["grant"]["label"]
    decision = authorization_registry.resolve(admin_user.tenant_id, "gate", 99, spawned_from_vehicle=True)
    assert decision.authorized
    authorization_registry.revoke(admin_user.tenant_id, body["grant"]["grant_id"])

    res = await auth_client.post(
        "/api/v1/security/vehicles/register",
        json={"camera_id": "gate", "track_id": 4, "plate_text": "MH00XX0000", "profile_type": "normal"},
    )
    assert res.status_code == 201
    assert res.json()["grant"] is None


@pytest.mark.asyncio
async def test_security_requires_auth(anon_client):
    res = await anon_client.get("/api/v1/security/state")
    assert res.status_code == 401
    res = await anon_client.put("/api/v1/security/arm", json={"mode": "armed"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_zone_semantics_roundtrip(auth_client, _isolated_zone_config):
    payload = {
        "name": "Back yard",
        "points": [{"x": 0.1, "y": 0.2}, {"x": 0.9, "y": 0.2}, {"x": 0.9, "y": 0.9}, {"x": 0.1, "y": 0.9}],
        "threshold_sec": 4,
        "camera_ids": ["cam-1"],
        "zone_type": "perimeter",
        "armed_schedule": {"mode": "schedule", "windows": [{"start": "22:00", "end": "06:00", "days": [0, 1, 2, 3, 4]}], "tz": "Asia/Kolkata"},
        "allowed_classes": ["Dog", "cat"],
    }
    res = await auth_client.post("/api/v1/zones/", json=payload)
    assert res.status_code == 201
    zone = res.json()
    assert zone["zone_type"] == "perimeter"
    assert zone["armed_schedule"]["mode"] == "schedule"
    assert zone["armed_schedule"]["tz"] == "Asia/Kolkata"
    assert zone["allowed_classes"] == ["cat", "dog"]
    assert "armed_now" in zone

    # Update without camera_ids must preserve the binding.
    update = dict(payload)
    update.pop("camera_ids")
    update["zone_type"] = "entrance"
    res = await auth_client.put(f"/api/v1/zones/{zone['zone_id']}", json=update)
    assert res.status_code == 200
    assert res.json()["camera_ids"] == ["cam-1"]
    assert res.json()["zone_type"] == "entrance"

    stored = json.loads(_isolated_zone_config.read_text())
    assert stored[0]["zone_type"] == "entrance"
    assert stored[0]["camera_ids"] == ["cam-1"]

    res = await auth_client.get("/api/v1/zones/types")
    assert res.status_code == 200
    assert {t["type"] for t in res.json()} >= {"restricted", "perimeter", "entrance", "driveway", "parking", "public"}


@pytest.mark.asyncio
async def test_zone_rejects_bad_schedule(auth_client):
    res = await auth_client.post(
        "/api/v1/zones/",
        json={
            "name": "bad",
            "points": [{"x": 0.1, "y": 0.2}, {"x": 0.9, "y": 0.2}, {"x": 0.9, "y": 0.9}],
            "armed_schedule": {"mode": "schedule", "windows": [{"start": "25:99", "end": "06:00"}]},
        },
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_camera_calibration_persisted_and_validated(auth_client):
    res = await auth_client.post(
        "/api/v1/cameras/",
        json={"name": "Lot", "location": "P1", "stream_url": "rtsp://8.8.8.8/live"},
    )
    assert res.status_code == 201
    cam = res.json()
    assert cam["calibration"] is None

    calibration = {
        "hfov_deg": 78.0,
        "homography_image_points": [[0.4, 0.9], [0.6, 0.9], [0.55, 0.6], [0.45, 0.6]],
        "homography_world_points": [[0, 0], [2.5, 0], [2.5, 5], [0, 5]],
    }
    res = await auth_client.put(f"/api/v1/cameras/{cam['id']}", json={"calibration": calibration})
    assert res.status_code == 200
    assert res.json()["calibration"]["hfov_deg"] == 78.0
    assert len(res.json()["calibration"]["homography_world_points"]) == 4

    res = await auth_client.get(f"/api/v1/cameras/{cam['id']}")
    assert res.json()["calibration"]["hfov_deg"] == 78.0

    bad = dict(calibration)
    bad["homography_world_points"] = [[0, 0], [2.5, 0]]
    res = await auth_client.put(f"/api/v1/cameras/{cam['id']}", json={"calibration": bad})
    assert res.status_code == 422

    res = await auth_client.put(f"/api/v1/cameras/{cam['id']}", json={"calibration": {"hfov_deg": 5}})
    assert res.status_code == 422
