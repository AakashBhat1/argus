"""Admin audit log: one row per audited action, never secrets, admin-only, tenant-scoped."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditEvent, AuthSession, Camera
from app.routers import auth as auth_router
from app.services import audit, camera_secrets
from app.services.auth import create_access_token
from app.services.login_attempts import LoginAttemptLimiter
from app.utils import utc_now
from argus_common.audit import AuditActor
from argus_common.web_auth import CSRF_HEADER, REFRESH_COOKIE

ORIGIN = "https://argus.test"
CSRF = {CSRF_HEADER: "1", "origin": ORIGIN}
SECRET_URL = "rtsp://admin:S3cret!@203.0.113.40:554/Streaming/Channels/101"


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    monkeypatch.setattr(auth_router, "login_attempt_limiter", LoginAttemptLimiter())


@pytest_asyncio.fixture()
async def camera_key(tmp_path, monkeypatch, test_session_factory):
    key = tmp_path / "camera-secrets.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    monkeypatch.setenv("CAMERA_SECRETS_KEY_FILE", str(key))
    get_settings.cache_clear()
    camera_secrets.reset_camera_secret_box()
    yield key
    # Shared test DB: drop rows sealed with this throwaway key.
    async with test_session_factory() as session:
        for camera in (await session.execute(select(Camera).where(Camera.name.like("audit-%")))).scalars():
            await session.delete(camera)
        await session.commit()
    get_settings.cache_clear()
    camera_secrets.reset_camera_secret_box()


def _client(app, user) -> AsyncClient:
    token = create_access_token(
        {"sub": user.username, "uid": user.id, "role": user.role, "tenant_id": user.tenant_id},
        expires_delta=timedelta(minutes=5),
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN, headers={"Authorization": f"Bearer {token}"})


async def _rows(session_factory, **where) -> list[AuditEvent]:
    async with session_factory() as session:
        query = select(AuditEvent).order_by(AuditEvent.created_at)
        for column, value in where.items():
            query = query.where(getattr(AuditEvent, column) == value)
        return list((await session.execute(query)).scalars())


async def test_camera_crud_writes_one_row_each_without_credentials(
    camera_key, app_with_db, admin_user, db_session, test_session_factory
):
    await db_session.commit()
    async with _client(app_with_db, admin_user) as admin:
        created = await admin.post("/api/v1/cameras/", json={"name": "audit-cam", "location": "L", "stream_url": SECRET_URL})
        assert created.status_code == 201, created.text
        camera_id = created.json()["id"]
        response = await admin.put(f"/api/v1/cameras/{camera_id}", json={"name": "audit-cam-2", "stream_url": SECRET_URL})
        assert response.status_code == 200
        response = await admin.delete(f"/api/v1/cameras/{camera_id}")
        assert response.status_code == 204

    rows = await _rows(test_session_factory, target_id=camera_id)
    assert [r.action for r in rows] == ["camera.create", "camera.update", "camera.delete"]
    assert all(r.actor == admin_user.username and r.tenant_id == admin_user.tenant_id for r in rows)
    assert "S3cret" not in repr([r.details for r in rows])
    assert rows[0].details["stream_url"].startswith("rtsp://***@203.0.113.40")


async def test_refused_change_writes_no_row(app_with_db, admin_user, db_session, test_session_factory):
    await db_session.commit()
    missing = str(uuid.uuid4())
    async with _client(app_with_db, admin_user) as admin:
        response = await admin.delete(f"/api/v1/cameras/{missing}")
        assert response.status_code == 404
    assert await _rows(test_session_factory, target_id=missing) == []


async def test_rolled_back_transaction_leaves_no_row(test_session_factory):
    target = uuid.uuid4().hex
    async with test_session_factory() as session:
        audit.record(session, None, AuditActor.system("tenant-1"), "camera.update", "camera", target)
        await session.rollback()
    assert await _rows(test_session_factory, target_id=target) == []


async def test_zone_and_security_actions_are_audited(app_with_db, admin_user, db_session, test_session_factory):
    await db_session.commit()
    zone = {"name": "Yard", "points": [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1}, {"x": 0.5, "y": 0.5}], "zone_type": "restricted"}
    async with _client(app_with_db, admin_user) as admin:
        zone_id = (await admin.post("/api/v1/zones/", json=zone)).json()["zone_id"]
        response = await admin.put(f"/api/v1/zones/{zone_id}", json={**zone, "name": "Yard 2"})
        assert response.status_code == 200
        response = await admin.delete(f"/api/v1/zones/{zone_id}")
        assert response.status_code == 204
        response = await admin.put("/api/v1/security/arm", json={"mode": "armed"})
        assert response.status_code == 200
        grant = (await admin.post("/api/v1/security/grants/site", json={"minutes": 5, "label": "Courier"})).json()
        response = await admin.delete(f"/api/v1/security/grants/{grant['grant_id']}")
        assert response.status_code == 204
        await admin.put("/api/v1/security/arm", json={"mode": "auto"})

    actions = [r.action for r in await _rows(test_session_factory, actor_id=admin_user.id)]
    for expected in ("zone.create", "zone.update", "zone.delete", "security.grant.create", "security.grant.revoke"):
        assert actions.count(expected) == 1, (expected, actions)
    assert actions.count("security.arm_mode.set") == 2


async def test_login_outcomes_and_session_events_are_audited(app_with_db, admin_user, db_session, test_session_factory):
    await db_session.commit()
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url=ORIGIN) as browser:
        bad = {"username": admin_user.username, "password": "wrong-password"}
        response = await browser.post("/api/v1/auth/session", data=bad, headers=CSRF)
        assert response.status_code == 401
        good = {"username": admin_user.username, "password": "adminpass"}
        response = await browser.post("/api/v1/auth/session", data=good, headers=CSRF)
        assert response.status_code == 200
        first = browser.cookies.get(REFRESH_COOKIE)
        response = await browser.post("/api/v1/auth/refresh", headers=CSRF)
        assert response.status_code == 200
        second = browser.cookies.get(REFRESH_COOKIE)

        # Replay the old refresh token after the grace window: the family is revoked.
        async with test_session_factory() as session:
            for row in (await session.execute(select(AuthSession).where(AuthSession.user_id == admin_user.id))).scalars():
                if row.used_at is not None:
                    row.used_at = utc_now() - timedelta(minutes=5)
            await session.commit()
        browser.cookies.clear()
        browser.cookies.set(REFRESH_COOKIE, first, domain="argus.test", path="/api/v1/auth")
        response = await browser.post("/api/v1/auth/refresh", headers=CSRF)
        assert response.status_code == 401

        browser.cookies.clear()
        browser.cookies.set(REFRESH_COOKIE, second, domain="argus.test", path="/api/v1/auth")
        response = await browser.post("/api/v1/auth/logout", headers=CSRF)
        assert response.status_code == 204

    actions = [r.action for r in await _rows(test_session_factory, actor=admin_user.username)]
    assert actions == ["auth.login.failure", "auth.login.success", "auth.session.reuse_revoked", "auth.logout"]
    assert all("wrong-password" not in repr(r.details) for r in await _rows(test_session_factory, actor=admin_user.username))


async def test_unknown_username_failure_is_recorded_truncated(app_with_db, test_session_factory):
    typed = "x" * 300
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url=ORIGIN) as browser:
        response = await browser.post("/api/v1/auth/session", data={"username": typed, "password": "p"}, headers=CSRF)
        assert response.status_code == 401
    rows = await _rows(test_session_factory, actor="x" * 64)
    assert [r.action for r in rows] == ["auth.login.failure"] and rows[0].actor_id is None


async def test_user_creation_is_audited(app_with_db, admin_user, db_session, test_session_factory):
    await db_session.commit()
    name = f"newop_{uuid.uuid4().hex[:6]}"
    async with _client(app_with_db, admin_user) as admin:
        created = await admin.post("/api/v1/auth/users", json={"username": name, "password": "Operator-Pass-123", "role": "operator"})
        assert created.status_code == 200, created.text
    rows = await _rows(test_session_factory, action="user.create", target_id=created.json()["id"])
    assert len(rows) == 1 and rows[0].details["via"] == "admin" and rows[0].details["username"] == name
    assert "Operator-Pass" not in repr(rows[0].details)


async def test_audit_api_is_admin_only_and_tenant_scoped(
    app_with_db, admin_user, operator_user, tenant2_user, db_session, test_session_factory
):
    await db_session.commit()
    async with test_session_factory() as session:
        audit.record(session, None, AuditActor.system("tenant-2"), "camera.update", "camera", "t2-cam")
        await session.commit()

    async with _client(app_with_db, operator_user) as op:
        response = await op.get("/api/v1/audit")
        assert response.status_code == 403
    async with _client(app_with_db, admin_user) as admin:
        await admin.put("/api/v1/security/arm", json={"mode": "auto"})
        page = (await admin.get("/api/v1/audit", params={"action": "security.*", "limit": 500})).json()
        assert page["items"] and all(e["tenant_id"] == "tenant-1" for e in page["items"])
        everything = (await admin.get("/api/v1/audit", params={"limit": 500})).json()["items"]
        assert all(e["target_id"] != "t2-cam" for e in everything)
        response = await admin.get("/api/v1/audit", params={"limit": 501})
        assert response.status_code == 422
        response = await admin.get("/api/v1/audit", params={"cursor": "%%%"})
        assert response.status_code == 422


async def test_require_admin_rejects_tenant2_operator_too(tenant2_user):
    from app.services.auth import require_admin

    with pytest.raises(HTTPException) as exc:
        await require_admin(tenant2_user)
    assert exc.value.status_code == 403
