"""Camera credentials: sealed at rest, never returned, kept across edits."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select, text, type_coerce
from sqlalchemy.types import Text

from app.config import get_settings
from app.models import Camera
from app.services import camera_secrets
from argus_common.secretbox import PREFIX

URL = "rtsp://admin:S3cret!@203.0.113.21:554/Streaming/Channels/101"


@pytest.fixture
async def camera_key(tmp_path, monkeypatch, test_session_factory):
    key = tmp_path / "camera-secrets.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    monkeypatch.setenv("CAMERA_SECRETS_KEY_FILE", str(key))
    get_settings.cache_clear()
    camera_secrets.reset_camera_secret_box()
    yield key
    # The test database is shared by the whole run and this key dies with the
    # test: drop what it sealed, or later tests would (rightly) fail to open it.
    async with test_session_factory() as session:
        await session.execute(text("DELETE FROM cameras WHERE stream_url LIKE 'enc:v1:%'"))
        await session.commit()
    get_settings.cache_clear()
    camera_secrets.reset_camera_secret_box()


async def _raw_url(db_session, camera_id):
    table = Camera.__table__
    return (
        await db_session.execute(select(type_coerce(table.c.stream_url, Text)).where(table.c.id == camera_id))
    ).scalar_one()


async def test_credentials_are_sealed_and_masked(camera_key, auth_client, db_session):
    body = {"name": "Gate cam", "location": "North", "stream_url": URL}
    created = await auth_client.post("/api/v1/cameras/", json=body)
    assert created.status_code == 201, created.text
    camera = created.json()
    assert "S3cret" not in camera["stream_url"] and "admin" not in camera["stream_url"]
    assert camera["stream_url"] == "rtsp://***@203.0.113.21:554/Streaming/Channels/101"

    raw = await _raw_url(db_session, camera["id"])
    assert raw.startswith(PREFIX) and "S3cret" not in raw
    listed = (await auth_client.get("/api/v1/cameras/")).json()
    assert all("S3cret" not in c["stream_url"] for c in listed)

    # The ORM still sees the real URL (the stream manager connects with it).
    stored = (await db_session.execute(select(Camera).where(Camera.id == camera["id"]))).scalar_one()
    assert stored.stream_url == URL


async def test_edits_with_the_masked_url_keep_the_credentials(camera_key, auth_client, db_session):
    camera = (await auth_client.post("/api/v1/cameras/", json={"name": "C", "location": "L", "stream_url": URL})).json()
    renamed = await auth_client.put(f"/api/v1/cameras/{camera['id']}", json={"name": "Renamed", "stream_url": camera["stream_url"]})
    assert renamed.status_code == 200, renamed.text
    db_session.expire_all()
    stored = (await db_session.execute(select(Camera).where(Camera.id == camera["id"]))).scalar_one()
    assert stored.stream_url == URL and stored.name == "Renamed"

    moved = camera["stream_url"].replace("203.0.113.21", "203.0.113.99")
    refused = await auth_client.put(f"/api/v1/cameras/{camera['id']}", json={"stream_url": moved})
    assert refused.status_code == 422 and "re-enter" in refused.text


async def test_operators_cannot_change_cameras(app_with_db, operator_user, sample_camera, db_session):
    from datetime import timedelta

    from httpx import ASGITransport, AsyncClient

    from app.services.auth import create_access_token

    await db_session.commit()
    token = create_access_token(
        {"sub": operator_user.username, "role": operator_user.role, "tenant_id": operator_user.tenant_id},
        expires_delta=timedelta(minutes=5),
    )
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t", headers={"Authorization": f"Bearer {token}"}) as op:
        assert (await op.get("/api/v1/cameras/")).status_code == 200
        body = {"name": "x", "location": "y", "stream_url": "rtsp://203.0.113.5/live"}
        assert (await op.post("/api/v1/cameras/", json=body)).status_code == 403
        assert (await op.put(f"/api/v1/cameras/{sample_camera.id}", json={"name": "z"})).status_code == 403
        assert (await op.delete(f"/api/v1/cameras/{sample_camera.id}")).status_code == 403


async def test_startup_seals_legacy_plaintext(camera_key, test_session_factory, db_session):
    camera_id = str(uuid.uuid4())
    await db_session.execute(
        text("INSERT INTO cameras (id, name, location, stream_url, tenant_id, status, resolution, fps, role, is_active) "
             "VALUES (:id, 'Legacy', 'L', :url, 'tenant-1', 'inactive', '1280x720', 30, 'surveillance', 1)"),
        {"id": camera_id, "url": URL},
    )
    await db_session.commit()
    assert await camera_secrets.seal_stored_stream_urls(test_session_factory) >= 1
    raw = await _raw_url(db_session, camera_id)
    assert raw.startswith(PREFIX)
    assert await camera_secrets.seal_stored_stream_urls(test_session_factory) == 0  # idempotent


def test_production_requires_the_key(monkeypatch):
    monkeypatch.setenv("DEBUG", "false")
    monkeypatch.delenv("CAMERA_SECRETS_KEY_FILE", raising=False)
    get_settings.cache_clear()
    camera_secrets.reset_camera_secret_box()
    try:
        with pytest.raises(RuntimeError, match="CAMERA_SECRETS_KEY_FILE"):
            camera_secrets.camera_secret_box()
    finally:
        get_settings.cache_clear()
        camera_secrets.reset_camera_secret_box()
