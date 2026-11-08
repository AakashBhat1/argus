"""Zones belong to one tenant: in the API, at runtime, and when bound to cameras."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.cli.import_zones import ZoneImportError, import_zones
from app.models import AuditEvent, Camera, RoiZoneRecord, User, UserRole
from app.services.auth import create_access_token, get_password_hash
from app.services.zone_store import zone_store

ZONE = {
    "name": "Yard",
    "points": [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1}, {"x": 0.5, "y": 0.5}],
    "zone_type": "restricted",
}


@pytest_asyncio.fixture(autouse=True)
async def _clean_zones(test_session_factory):
    async def wipe() -> None:
        async with test_session_factory() as session:
            await session.execute(delete(RoiZoneRecord))
            await session.commit()
        await zone_store.load_all(test_session_factory)

    await wipe()
    yield
    await wipe()


@pytest_asyncio.fixture()
async def tenant2_admin(db_session) -> User:
    user = User(
        id=str(uuid.uuid4()),
        username=f"t2admin_{uuid.uuid4().hex[:6]}",
        hashed_password=get_password_hash("t2pass"),
        role=UserRole.ADMIN.value,
        tenant_id="tenant-2",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _client(app, user) -> AsyncClient:
    token = create_access_token(
        {"sub": user.username, "uid": user.id, "role": user.role, "tenant_id": user.tenant_id},
        expires_delta=timedelta(minutes=5),
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers={"Authorization": f"Bearer {token}"})


async def _create(client, **overrides) -> dict:
    response = await client.post("/api/v1/zones/", json={**ZONE, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_tenant_cannot_see_or_change_another_tenants_zones(app_with_db, admin_user, tenant2_admin, db_session):
    await db_session.commit()
    async with _client(app_with_db, admin_user) as tenant1, _client(app_with_db, tenant2_admin) as tenant2:
        zone = await _create(tenant1)
        listing = await tenant2.get("/api/v1/zones/")
        assert listing.status_code == 200 and listing.json() == []
        changed = await tenant2.put(f"/api/v1/zones/{zone['zone_id']}", json={**ZONE, "name": "Hijacked"})
        assert changed.status_code == 404
        removed = await tenant2.delete(f"/api/v1/zones/{zone['zone_id']}")
        assert removed.status_code == 404
        kept = await tenant1.get("/api/v1/zones/")
    assert [z["name"] for z in kept.json()] == ["Yard"]


async def test_zones_cannot_be_bound_to_another_tenants_camera(app_with_db, admin_user, tenant2_admin, sample_camera, db_session):
    await db_session.commit()
    async with _client(app_with_db, tenant2_admin) as tenant2:
        refused = await tenant2.post("/api/v1/zones/", json={**ZONE, "camera_ids": [sample_camera.id]})
    assert refused.status_code == 422
    async with _client(app_with_db, admin_user) as tenant1:
        await _create(tenant1, camera_ids=[sample_camera.id])


async def test_runtime_applies_only_the_cameras_own_tenants_zones(app_with_db, admin_user, tenant2_admin, db_session):
    await db_session.commit()
    async with _client(app_with_db, admin_user) as tenant1:
        await _create(tenant1)  # no camera list: every camera of tenant-1
    assert [z.name for z in zone_store.for_tenant("tenant-1").zones_for_camera("any-camera")] == ["Yard"]
    assert zone_store.for_tenant("tenant-2").zones_for_camera("any-camera") == []


async def test_changes_reach_the_runtime_immediately(app_with_db, admin_user, db_session):
    await db_session.commit()
    view = zone_store.for_tenant("tenant-1")
    async with _client(app_with_db, admin_user) as admin:
        zone = await _create(admin)
        assert [z.name for z in view.zones_for_camera("c")] == ["Yard"]
        await admin.put(f"/api/v1/zones/{zone['zone_id']}", json={**ZONE, "name": "Dock"})
        assert [z.name for z in view.zones_for_camera("c")] == ["Dock"]
        await admin.delete(f"/api/v1/zones/{zone['zone_id']}")
    assert view.zones_for_camera("c") == []


async def test_load_all_rebuilds_the_runtime_from_the_database(test_session_factory):
    async with test_session_factory() as session:
        session.add(RoiZoneRecord(
            tenant_id="tenant-9", name="Loaded", points=[[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]],
            threshold_sec=5.0, color=[0, 255, 255], zone_type="perimeter",
            armed_schedule={"mode": "always"}, allowed_classes=[],
        ))
        await session.commit()
    await zone_store.load_all(test_session_factory)
    zones = zone_store.for_tenant("tenant-9").zones_for_camera("c")
    assert [(z.name, z.zone_type) for z in zones] == [("Loaded", "perimeter")]


async def test_a_failed_change_leaves_neither_zone_nor_audit_row(app_with_db, admin_user, db_session, test_session_factory, monkeypatch):
    await db_session.commit()
    from app.routers import zones as zones_router

    def boom(*args, **kwargs):
        raise RuntimeError("audit failed")

    monkeypatch.setattr(zones_router.audit, "record", boom)
    transport = ASGITransport(app=app_with_db, raise_app_exceptions=False)
    token = create_access_token(
        {"sub": admin_user.username, "uid": admin_user.id, "role": admin_user.role, "tenant_id": admin_user.tenant_id},
        expires_delta=timedelta(minutes=5),
    )
    async with AsyncClient(transport=transport, base_url="http://t", headers={"Authorization": f"Bearer {token}"}) as admin:
        response = await admin.post("/api/v1/zones/", json=ZONE)
    assert response.status_code == 500
    async with test_session_factory() as session:
        assert (await session.execute(select(RoiZoneRecord))).first() is None


async def test_import_cli_assigns_a_tenant_and_drops_foreign_cameras(tmp_path, test_session_factory, sample_camera, db_session):
    await db_session.commit()
    source = tmp_path / "zones.json"
    source.write_text(json.dumps([
        {"zone_id": 7, "name": "Old", "points": [[100, 50], [500, 50], [500, 400]], "reference_width": 1000,
         "reference_height": 500, "camera_ids": [sample_camera.id, "someone-elses-camera"]},
    ]), encoding="utf-8")

    assert await import_zones(source, "tenant-1", session_factory=test_session_factory) == 1

    async with test_session_factory() as session:
        row = (await session.execute(select(RoiZoneRecord).where(RoiZoneRecord.tenant_id == "tenant-1"))).scalar_one()
        audited = (await session.execute(select(AuditEvent).where(AuditEvent.action == "zone.import", AuditEvent.target_id == str(row.id)))).scalars().all()
        camera_exists = await session.get(Camera, sample_camera.id)
    assert camera_exists is not None
    assert row.camera_ids == [sample_camera.id]
    assert row.points[0] == pytest.approx([0.1, 0.1])
    assert len(audited) == 1
    with pytest.raises(ZoneImportError, match="already has zones"):
        await import_zones(source, "tenant-1", session_factory=test_session_factory)
