"""Internal events API: peer authentication, authorization and idempotency."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.mesh import mesh
from app.models import Alert
from app.services.authorization import authorization_registry
from app.services.websocket_manager import ws_manager
from argus_common.events import (
    EventEnvelope,
    ParkingAlertRaised,
    VehicleGatePassed,
    VehicleTheftSuspected,
)
from argus_common.keys import SigningKey
from argus_common.service_http import MTLS_CLIENT_SERVICE_HEADER, MTLS_VERIFIED_HEADER, SERVICE_TOKEN_HEADER
from argus_common.tokens import PeerPolicy, ServiceTokenSigner, ServiceTokenVerifier

from app.mesh import PEER_SCOPES


@pytest.fixture
def peers():
    keys = {"parking": SigningKey.generate(), "face": SigningKey.generate()}
    verifier = ServiceTokenVerifier(
        "surveillance",
        {
            name: PeerPolicy({key.kid: key.public_key}, frozenset(PEER_SCOPES[name]))
            for name, key in keys.items()
        },
    )
    mesh.configure_for_tests(ServiceTokenSigner("surveillance", SigningKey.generate()), verifier)
    yield {name: ServiceTokenSigner(name, key) for name, key in keys.items()}
    mesh.reset()


@pytest.fixture
async def internal_client(app_with_db, monkeypatch):
    monkeypatch.setattr(ws_manager, "broadcast_alert", AsyncMock())
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://internal") as client:
        yield client


def _headers(signer, scopes=("events:publish",), mtls=True, cert_service=None):
    headers = {SERVICE_TOKEN_HEADER: signer.token_for("surveillance", scopes)}
    if mtls:
        headers[MTLS_VERIFIED_HEADER] = "SUCCESS"
        headers[MTLS_CLIENT_SERVICE_HEADER] = cert_service or signer.service
    return headers


def _alert_event(tenant="tenant-events", source="parking", **overrides):
    data = dict(
        alert_id="pk-alert-1",
        alert_type="lane_loitering",
        severity="high",
        camera_id="lot-cam-1",
        description="Person loitering in lane",
        space_id="P-07",
    )
    data.update(overrides)
    return EventEnvelope.create("parking.alert", source, tenant, ParkingAlertRaised(**data)).to_dict()


async def test_parking_alert_is_recorded_once_and_broadcast(peers, internal_client, db_session):
    event = _alert_event()
    response = await internal_client.post("/internal/v1/events", json=event, headers=_headers(peers["parking"]))
    assert response.status_code == 202, response.text
    again = await internal_client.post("/internal/v1/events", json=event, headers=_headers(peers["parking"]))
    assert again.status_code == 200 and again.json()["status"] == "duplicate"

    rows = (await db_session.execute(select(Alert).where(Alert.tenant_id == "tenant-events"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "parking" and rows[0].camera_id is None
    assert rows[0].metadata_["space_id"] == "P-07"
    ws_manager.broadcast_alert.assert_awaited_once()


async def test_authorized_gate_entry_grants_vehicle_occupants(peers, internal_client):
    tenant = "tenant-gate"
    event = EventEnvelope.create(
        "vehicle.gate_passed",
        "parking",
        tenant,
        VehicleGatePassed(plate="KA01AB1234", direction="entry", camera_id="gate-1", profile_type="resident", authorized=True),
    ).to_dict()
    response = await internal_client.post("/internal/v1/events", json=event, headers=_headers(peers["parking"]))
    assert response.status_code == 202
    decision = authorization_registry.resolve(tenant, "cam-x", 5, spawned_from_vehicle=True)
    assert decision.authorized and "KA01AB1234" in decision.label
    # A person who did not step out of a vehicle gains nothing.
    assert not authorization_registry.resolve(tenant, "cam-x", 6).authorized


async def test_blacklisted_vehicle_and_theft_raise_alerts(peers, internal_client, db_session):
    tenant = "tenant-threat"
    blacklisted = EventEnvelope.create(
        "vehicle.gate_passed", "parking", tenant,
        VehicleGatePassed(plate="MH12XY9999", direction="entry", camera_id="gate-1", profile_type="blacklist", threat=True),
    ).to_dict()
    theft = EventEnvelope.create(
        "vehicle.theft_suspected", "parking", tenant,
        VehicleTheftSuspected(plate="KA01AB1234", reason="driver not authorized for this vehicle"),
    ).to_dict()
    for event in (blacklisted, theft):
        response = await internal_client.post("/internal/v1/events", json=event, headers=_headers(peers["parking"]))
        assert response.status_code == 202
    types = {a.type: a.severity for a in (await db_session.execute(select(Alert).where(Alert.tenant_id == tenant))).scalars()}
    assert types == {"blacklisted_vehicle": "high", "vehicle_theft_suspected": "critical"}
    assert not authorization_registry.resolve(tenant, "c", 1, spawned_from_vehicle=True).authorized


async def test_missing_token_or_user_token_is_rejected(peers, internal_client, auth_client):
    event = _alert_event()
    assert (await internal_client.post("/internal/v1/events", json=event, headers={MTLS_VERIFIED_HEADER: "SUCCESS"})).status_code == 401
    user_token = auth_client.headers["Authorization"].split()[1]
    forged = {SERVICE_TOKEN_HEADER: user_token, MTLS_VERIFIED_HEADER: "SUCCESS"}
    assert (await internal_client.post("/internal/v1/events", json=event, headers=forged)).status_code == 401


async def test_mtls_header_is_required(peers, internal_client):
    response = await internal_client.post(
        "/internal/v1/events", json=_alert_event(), headers=_headers(peers["parking"], mtls=False)
    )
    assert response.status_code == 403


async def test_peer_cannot_impersonate_another_source(peers, internal_client):
    spoofed = _alert_event(source="face")
    response = await internal_client.post("/internal/v1/events", json=spoofed, headers=_headers(peers["parking"]))
    assert response.status_code == 403


async def test_peer_without_publish_scope_is_rejected(peers, internal_client):
    response = await internal_client.post(
        "/internal/v1/events", json=_alert_event(source="face"), headers=_headers(peers["face"])
    )
    assert response.status_code == 403


async def test_unaccepted_event_type_and_bad_payload_are_rejected(peers, internal_client):
    arm = {
        "id": "e1", "type": "security.arm_mode_changed", "source": "parking", "tenant_id": "t",
        "occurred_at": "2026-01-01T00:00:00Z", "data": {"mode": "armed"}, "version": 1,
    }
    assert (await internal_client.post("/internal/v1/events", json=arm, headers=_headers(peers["parking"]))).status_code == 422
    bad = _alert_event()
    bad["data"]["severity"] = "apocalyptic"
    assert (await internal_client.post("/internal/v1/events", json=bad, headers=_headers(peers["parking"]))).status_code == 422


async def test_internal_routes_are_not_in_public_schema(app_with_db):
    assert not any(path.startswith("/internal") for path in app_with_db.openapi()["paths"])


async def test_token_must_match_the_client_certificate(peers, internal_client):
    response = await internal_client.post(
        "/internal/v1/events", json=_alert_event(), headers=_headers(peers["parking"], cert_service="face")
    )
    assert response.status_code == 403
