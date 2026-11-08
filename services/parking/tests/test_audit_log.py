"""Parking audit log and the two-step (propose, then confirm) ParkBot."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update

from app.models import (
    AuditEvent, Camera, DetectedPlate, ParkbotProposal, ParkingActivityLog, ParkingSession,
    ParkingSpace, VehicleProfile,
)
from app.services import audit, retention
from app.services.parking_assistant import parking_assistant
from app.utils import utc_now
from argus_common.audit import AuditActor
from support import identity
from support.identity import User, UserRole

PLATE = "MH12AB1234"
SECRET_URL = "rtsp://admin:S3cret!@203.0.113.50:554/Streaming/Channels/101"


@pytest_asyncio.fixture(autouse=True)
async def clean_tables(db_session):
    for model in (ParkingActivityLog, ParkingSession, DetectedPlate, ParkingSpace, VehicleProfile, ParkbotProposal):
        await db_session.execute(delete(model))
    await db_session.execute(delete(Camera).where(Camera.name.like("audit-%")))
    await db_session.commit()
    yield


@pytest.fixture(autouse=True)
def offline_llm(monkeypatch):
    # The model only chats; the action comes from the admin's JSON message.
    monkeypatch.setattr(parking_assistant, "_ollama_chat", AsyncMock(return_value="Sure, here is the plan."))
    monkeypatch.setattr(parking_assistant, "_build_context", AsyncMock(return_value="ctx"))


def _client(app, user) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {identity.token_for(user)}"},
    )


def _admin(tenant: str = "tenant-1") -> User:
    return User(username=f"admin_{uuid.uuid4().hex[:6]}", role=UserRole.ADMIN.value, tenant_id=tenant)


async def _rows(session_factory, **where) -> list[AuditEvent]:
    async with session_factory() as session:
        query = select(AuditEvent).order_by(AuditEvent.created_at)
        for column, value in where.items():
            query = query.where(getattr(AuditEvent, column) == value)
        return list((await session.execute(query)).scalars())


async def test_camera_crud_is_audited_without_credentials(app_with_db, test_session_factory):
    admin = _admin()
    async with _client(app_with_db, admin) as client:
        created = await client.post(
            "/api/v1/parking/cameras", json={"name": "audit-gate", "location": "Gate", "stream_url": SECRET_URL}
        )
        assert created.status_code == 201, created.text
        camera_id = created.json()["id"]
        assert (await client.put(f"/api/v1/parking/cameras/{camera_id}", json={"name": "audit-gate-2"})).status_code == 200
        assert (await client.delete(f"/api/v1/parking/cameras/{camera_id}")).status_code == 204
    rows = await _rows(test_session_factory, target_id=camera_id)
    assert [r.action for r in rows] == ["camera.create", "camera.update", "camera.delete"]
    assert all(r.actor == admin.username for r in rows)
    assert "S3cret" not in repr([r.details for r in rows])


async def test_parkbot_only_acts_after_confirmation(app_with_db, test_session_factory):
    admin = _admin()
    message = f'{{"action": "assign_space", "plate_text": "{PLATE}"}}'
    async with _client(app_with_db, admin) as client:
        await client.get("/api/v1/parking/spaces")  # seeds the tenant's bays
        proposed = (await client.post("/api/v1/parking/chat/command", json={"message": message})).json()
        assert proposed["requires_confirmation"] is True and proposed["executed"] is False
        assert proposed["command"] == {"action": "assign_space", "plate_text": PLATE}

        async with test_session_factory() as session:
            assert (await session.execute(select(VehicleProfile).where(VehicleProfile.plate_text == PLATE))).first() is None

        confirmed = await client.post("/api/v1/parking/chat/confirm", json={"proposal_id": proposed["proposal_id"]})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["executed"] is True and confirmed.json()["result"]["ok"] is True

        replay = await client.post("/api/v1/parking/chat/confirm", json={"proposal_id": proposed["proposal_id"]})
        assert replay.status_code == 409

    actions = [r.action for r in await _rows(test_session_factory, actor=admin.username)]
    assert actions == [
        "parkbot.command.proposed", "vehicle_profile.create", "parkbot.command.executed",
        "parkbot.command.confirm_refused",
    ]
    proposed_row = (await _rows(test_session_factory, action="parkbot.command.proposed", actor=admin.username))[0]
    assert proposed_row.details["message"] == message and proposed_row.details["validation"] == "ok"


async def test_proposal_is_bound_to_its_admin_tenant_and_ttl(app_with_db, test_session_factory):
    owner, other_admin, foreign_admin = _admin(), _admin(), _admin("tenant-2")
    message = '{"action": "release_space", "space_id": "G-01"}'
    async with _client(app_with_db, owner) as client:
        proposal_id = (await client.post("/api/v1/parking/chat/command", json={"message": message})).json()["proposal_id"]
    for intruder in (other_admin, foreign_admin):
        async with _client(app_with_db, intruder) as client:
            assert (await client.post("/api/v1/parking/chat/confirm", json={"proposal_id": proposal_id})).status_code == 409

    async with test_session_factory() as session:
        await session.execute(
            update(ParkbotProposal).where(ParkbotProposal.id == proposal_id).values(expires_at=utc_now() - timedelta(seconds=1))
        )
        await session.commit()
    async with _client(app_with_db, owner) as client:
        assert (await client.post("/api/v1/parking/chat/confirm", json={"proposal_id": proposal_id})).status_code == 409


async def test_rejected_command_is_audited_and_operators_cannot_command(app_with_db, operator_user, test_session_factory):
    admin = _admin()
    async with _client(app_with_db, admin) as client:
        body = (await client.post("/api/v1/parking/chat/command", json={"message": '{"action": "drop_table"}'})).json()
        assert body["executed"] is False and body["error"]
    rows = await _rows(test_session_factory, actor=admin.username)
    assert [r.action for r in rows] == ["parkbot.command.rejected"]
    async with _client(app_with_db, operator_user) as client:
        assert (await client.post("/api/v1/parking/chat/confirm", json={"proposal_id": str(uuid.uuid4())})).status_code == 403
        assert (await client.get("/api/v1/parking/audit")).status_code == 403


async def test_manual_release_is_audited(app_with_db, test_session_factory, db_session):
    admin = _admin()
    async with _client(app_with_db, admin) as client:
        await client.get("/api/v1/parking/spaces")
        from app.services import parking_service

        async with test_session_factory() as session:
            assigned = await parking_service.assign_space(session, "tenant-1", PLATE, actor=AuditActor.user(admin))
            await session.commit()
        released = await client.post(f"/api/v1/parking/spaces/{assigned['space_id']}/release")
        assert released.status_code == 200, released.text
    rows = await _rows(test_session_factory, action="parking.space.release", actor=admin.username)
    assert len(rows) == 1 and rows[0].details["plate"] == PLATE


async def test_audit_api_is_tenant_scoped(app_with_db, test_session_factory):
    async with test_session_factory() as session:
        audit.record(session, None, AuditActor.system("tenant-2"), "camera.update", "camera", "t2-only")
        audit.record(session, None, AuditActor.system("tenant-1"), "camera.update", "camera", "t1-only")
        await session.commit()
    async with _client(app_with_db, _admin()) as client:
        items = (await client.get("/api/v1/parking/audit", params={"limit": 500})).json()["items"]
    targets = {item["target_id"] for item in items}
    assert "t1-only" in targets and "t2-only" not in targets


async def test_retention_purges_old_rows_and_spent_proposals(test_session_factory, monkeypatch):
    monkeypatch.setattr(retention.database, "get_session_factory", lambda: test_session_factory)
    async with test_session_factory() as session:
        old = audit.record(session, None, AuditActor.system("tenant-1"), "camera.update", "camera", "ancient")
        old.created_at = utc_now() - timedelta(days=400)
        session.add(ParkbotProposal(tenant_id="tenant-1", actor_id="a", command={}, expires_at=utc_now() - timedelta(minutes=1)))
        await session.commit()
    audit_count, proposal_count = await retention.purge_once()
    assert audit_count >= 1 and proposal_count >= 1
    assert await _rows(test_session_factory, target_id="ancient") == []
