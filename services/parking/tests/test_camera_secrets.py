"""Parking camera credentials: sealed at rest, never returned, kept across edits."""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text, type_coerce
from sqlalchemy.types import Text

from app.config import get_settings
from app.models import Camera
from app.services import camera_secrets
from argus_common.secretbox import PREFIX
from support.identity import token_for

URL = "rtsp://gate:Pa55word@203.0.113.31:554/live"


@pytest.fixture
async def camera_key(tmp_path, monkeypatch, test_session_factory):
    key = tmp_path / "camera-secrets.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    monkeypatch.setenv("CAMERA_SECRETS_KEY_FILE", str(key))
    get_settings.cache_clear()
    camera_secrets.reset_camera_secret_box()
    yield key
    async with test_session_factory() as session:
        await session.execute(text("DELETE FROM cameras WHERE stream_url LIKE 'enc:v1:%'"))
        await session.commit()
    get_settings.cache_clear()
    camera_secrets.reset_camera_secret_box()


async def test_sealed_masked_and_kept_across_edits(camera_key, app_with_db, admin_user, db_session):
    headers = {"Authorization": f"Bearer {token_for(admin_user)}"}
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t", headers=headers) as admin:
        created = await admin.post("/api/v1/parking/cameras", json={"name": "Gate", "location": "N", "stream_url": URL, "role": "gate_entry"})
        assert created.status_code == 201, created.text
        camera = created.json()
        assert camera["stream_url"] == "rtsp://***@203.0.113.31:554/live"

        table = Camera.__table__
        raw = (await db_session.execute(select(type_coerce(table.c.stream_url, Text)).where(table.c.id == camera["id"]))).scalar_one()
        assert raw.startswith(PREFIX) and "Pa55word" not in raw

        assert (await admin.put(f"/api/v1/parking/cameras/{camera['id']}", json={"name": "Gate 2", "stream_url": camera["stream_url"]})).status_code == 200
        db_session.expire_all()
        stored = (await db_session.execute(select(Camera).where(Camera.id == camera["id"]))).scalar_one()
        assert stored.stream_url == URL and stored.name == "Gate 2"

        moved = camera["stream_url"].replace("203.0.113.31", "203.0.113.32")
        assert (await admin.put(f"/api/v1/parking/cameras/{camera['id']}", json={"stream_url": moved})).status_code == 422
