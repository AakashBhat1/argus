"""Parking as a separate service: events, internal API, auth, camera control."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.mesh import PEER_SCOPES, mesh
from app.models import Alert, AlertSeverity, OutboxEvent, VehicleProfile
from app.services import gate_ocr as gate_ocr_module
from app.services.gate_ocr import GateOcrTrigger
from app.services.parking_occupancy_service import persist_anomaly_alerts
from app.services.site_state import site_state
from app.services.websocket_manager import ws_manager
from argus_common.events import ArmModeChanged, EventEnvelope
from argus_common.keys import SigningKey
from argus_common.service_http import MTLS_VERIFIED_HEADER, SERVICE_TOKEN_HEADER
from argus_common.tokens import PeerPolicy, ServiceTokenSigner, ServiceTokenVerifier, UserTokenIssuer
from support.identity import AUDIENCE, ISSUER, User, token_for


@pytest.fixture
def with_surveillance_peer(monkeypatch):
    """Configure parking as if deployed next to a surveillance service."""
    monkeypatch.setenv("SURVEILLANCE_INTERNAL_URL", "https://surveillance-internal:8443")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _outbox(db_session, event_type):
    rows = (await db_session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type))).scalars().all()
    return [row for row in rows]


async def test_gate_read_stages_vehicle_event_atomically(app_with_db, db_session, with_surveillance_peer, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_to_channel", AsyncMock())
    tenant = "tenant-gate-events"
    db_session.add(VehicleProfile(tenant_id=tenant, plate_text="KA01AB1234", profile_type="resident", owner_name="Asha"))
    await db_session.commit()

    trigger = GateOcrTrigger("gate-cam", tenant)
    trigger.bind_loop(asyncio.get_running_loop())
    trigger.update_meta("gate_entry", [[0, 0], [1, 0], [1, 1], [0, 1]])
    car = {"object_id": 3, "class_label": "car", "bbox_x": 100, "bbox_y": 100, "bbox_w": 200, "bbox_h": 120}
    with patch.object(gate_ocr_module, "recognize_plate", lambda crop: ("KA01AB1234", "Karnataka", 0.93)):
        trigger.process_frame([car], np.zeros((480, 640, 3), dtype=np.uint8))
        for _ in range(50):
            await asyncio.sleep(0.02)
            if not trigger._pending:
                break

    rows = [r for r in await _outbox(db_session, "vehicle.gate_passed") if r.tenant_id == tenant]
    assert len(rows) == 1
    envelope = EventEnvelope.from_dict(rows[0].envelope)
    data = envelope.validated_data()
    assert (data.plate, data.direction, data.authorized, data.threat) == ("KA01AB1234", "entry", True, False)
    assert rows[0].destination == "surveillance" and rows[0].status == "pending"


async def test_blacklisted_plate_event_carries_threat(app_with_db, db_session, with_surveillance_peer, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_to_channel", AsyncMock())
    tenant = "tenant-bl"
    db_session.add(VehicleProfile(tenant_id=tenant, plate_text="MH12XY9999", profile_type="blacklist"))
    await db_session.commit()
    trigger = GateOcrTrigger("gate-cam", tenant)
    trigger.bind_loop(asyncio.get_running_loop())
    trigger.update_meta("gate_exit", [[0, 0], [1, 0], [1, 1], [0, 1]])
    car = {"object_id": 9, "class_label": "car", "bbox_x": 10, "bbox_y": 10, "bbox_w": 100, "bbox_h": 80}
    with patch.object(gate_ocr_module, "recognize_plate", lambda crop: ("MH12XY9999", None, 0.9)):
        trigger.process_frame([car], np.zeros((240, 320, 3), dtype=np.uint8))
        for _ in range(50):
            await asyncio.sleep(0.02)
            if not trigger._pending:
                break
    rows = [r for r in await _outbox(db_session, "vehicle.gate_passed") if r.tenant_id == tenant]
    data = EventEnvelope.from_dict(rows[0].envelope).validated_data()
    assert data.threat is True and data.authorized is False and data.direction == "exit"


async def test_anomaly_alerts_are_forwarded_to_surveillance(app_with_db, db_session, with_surveillance_peer, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_alert", AsyncMock())
    alert = Alert(
        camera_id=None,
        tenant_id="tenant-alerts",
        type="car_hopping",
        severity=AlertSeverity.HIGH.value,
        description="Person moving between parked cars",
        metadata_={"space_id": "P-03"},
    )
    await persist_anomaly_alerts([alert])
    rows = [r for r in await _outbox(db_session, "parking.alert") if r.tenant_id == "tenant-alerts"]
    assert len(rows) == 1
    data = EventEnvelope.from_dict(rows[0].envelope).validated_data()
    assert data.alert_type == "car_hopping" and data.space_id == "P-03" and data.alert_id == alert.id


async def test_standalone_parking_publishes_nothing(app_with_db, db_session, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_alert", AsyncMock())
    alert = Alert(tenant_id="tenant-standalone", type="ghost_occupancy", severity="medium", description="x")
    await persist_anomaly_alerts([alert])
    rows = [r for r in await _outbox(db_session, "parking.alert") if r.tenant_id == "tenant-standalone"]
    assert rows == []


# -- internal API ----------------------------------------------------------------


@pytest.fixture
def surveillance_signer():
    key = SigningKey.generate()
    verifier = ServiceTokenVerifier(
        "parking", {"surveillance": PeerPolicy({key.kid: key.public_key}, frozenset(PEER_SCOPES["surveillance"]))}
    )
    mesh.configure_for_tests(ServiceTokenSigner("parking", SigningKey.generate()), verifier)
    yield ServiceTokenSigner("surveillance", key)
    mesh.reset()


async def test_arm_mode_event_updates_site_state(app_with_db, surveillance_signer, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_to_channel", AsyncMock())
    envelope = EventEnvelope.create(
        "security.arm_mode_changed", "surveillance", "tenant-arm", ArmModeChanged(mode="armed", actor="admin")
    ).to_dict()
    headers = {
        SERVICE_TOKEN_HEADER: surveillance_signer.token_for("parking", ["events:publish"]),
        MTLS_VERIFIED_HEADER: "SUCCESS",
    }
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://internal") as client:
        response = await client.post("/internal/v1/events", json=envelope, headers=headers)
        assert response.status_code == 202, response.text
        assert site_state.arm_mode("tenant-arm") == "armed"
        # Missing mTLS header / token -> refused.
        assert (await client.post("/internal/v1/events", json=envelope)).status_code in (401, 403)


# -- user auth -------------------------------------------------------------------


async def test_tokens_from_an_untrusted_issuer_key_are_rejected(anon_client):
    rogue = UserTokenIssuer(SigningKey.generate(), ISSUER, AUDIENCE).issue(
        subject="mallory", tenant_id="tenant-1", role="admin", ttl_seconds=300
    )
    response = await anon_client.get("/api/v1/parking/cameras", headers={"Authorization": f"Bearer {rogue}"})
    assert response.status_code == 401


def test_parking_refuses_to_run_without_a_trusted_identity_provider(monkeypatch):
    from app.services import auth

    previous = auth._verifier
    auth.set_token_verifier(None)
    try:
        with pytest.raises(RuntimeError, match="AUTH_JWKS"):
            auth.token_verifier()
    finally:
        auth.set_token_verifier(previous)


# -- cameras ---------------------------------------------------------------------


async def test_camera_admin_only_and_ssrf_checked(app_with_db, operator_user, admin_user):
    transport = ASGITransport(app=app_with_db)
    body = {"name": "Gate A", "location": "North gate", "stream_url": "rtsp://169.254.169.254/x", "role": "gate_entry"}
    async with AsyncClient(transport=transport, base_url="http://t", headers={"Authorization": f"Bearer {token_for(operator_user)}"}) as op:
        assert (await op.post("/api/v1/parking/cameras", json=body)).status_code == 403
    async with AsyncClient(transport=transport, base_url="http://t", headers={"Authorization": f"Bearer {token_for(admin_user)}"}) as admin:
        response = await admin.post("/api/v1/parking/cameras", json=body)
        assert response.status_code == 422 and "metadata" in response.json()["detail"]
        body["stream_url"] = "rtsp://203.0.113.40/live"
        created = await admin.post("/api/v1/parking/cameras", json=body)
        assert created.status_code == 201
        assert created.json()["service"] == "parking"
        body["role"] = "surveillance"
        assert (await admin.post("/api/v1/parking/cameras", json=body)).status_code == 422


async def test_cameras_are_tenant_isolated(app_with_db, admin_user):
    transport = ASGITransport(app=app_with_db)
    other = User(username="other_admin", role="admin", tenant_id="tenant-9")
    async with AsyncClient(transport=transport, base_url="http://t", headers={"Authorization": f"Bearer {token_for(admin_user)}"}) as a:
        cam = (await a.post("/api/v1/parking/cameras", json={
            "name": "Lot", "location": "L1", "stream_url": "rtsp://203.0.113.41/live", "role": "parking",
        })).json()
    async with AsyncClient(transport=transport, base_url="http://t", headers={"Authorization": f"Bearer {token_for(other)}"}) as b:
        assert (await b.get(f"/api/v1/parking/cameras/{cam['id']}")).status_code == 404
        assert all(c["id"] != cam["id"] for c in (await b.get("/api/v1/parking/cameras")).json())
