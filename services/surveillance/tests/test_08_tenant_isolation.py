"""
TEST 8 - Tenant Isolation in Queries

RED: Camera queries lack WHERE tenant_id = ? filter. A tenant-1 user
     can see tenant-2 cameras.

GREEN: Extract tenant_id from get_current_active_user and add
       .where(Camera.tenant_id == current_user.tenant_id) to all queries.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from datetime import timedelta
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import Camera, User
from app.services.auth import create_access_token


def _token_for(user: User) -> str:
    return create_access_token(
        data={
            "sub": user.username,
            "role": user.role,
            "tenant_id": user.tenant_id,
        },
        expires_delta=timedelta(minutes=30),
    )


@pytest_asyncio.fixture()
async def tenant1_camera(db_session: AsyncSession) -> Camera:
    cam = Camera(
        id=str(uuid.uuid4()),
        name="Tenant1 Camera",
        location="HQ",
        stream_url="rtsp://10.0.1.1/live",
        tenant_id="tenant-1",
    )
    db_session.add(cam)
    await db_session.flush()
    return cam


@pytest_asyncio.fixture()
async def tenant2_camera(db_session: AsyncSession) -> Camera:
    cam = Camera(
        id=str(uuid.uuid4()),
        name="Tenant2 Camera",
        location="Branch",
        stream_url="rtsp://10.0.2.1/live",
        tenant_id="tenant-2",
    )
    db_session.add(cam)
    await db_session.flush()
    return cam


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_list_cameras_only_returns_own_tenant(
        self,
        app_with_db,
        operator_user,
        tenant1_camera,
        tenant2_camera,
    ):
        """
        RED: A tenant-1 user must NOT see tenant-2 cameras.
        Currently all cameras are returned regardless of tenant.
        """
        token = _token_for(operator_user)
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            response = await client.get("/api/v1/cameras/")

        assert response.status_code == 200
        ids = [cam["id"] for cam in response.json()]

        assert tenant1_camera.id in ids, "Should see own camera"
        assert tenant2_camera.id not in ids, (
            "RED: tenant-2 camera must not be visible to tenant-1 user"
        )

    @pytest.mark.asyncio
    async def test_get_camera_by_id_enforces_tenant(
        self,
        app_with_db,
        operator_user,
        tenant2_camera,
    ):
        """
        RED: Fetching a tenant-2 camera by ID as a tenant-1 user must
        return 404, not 200.
        """
        token = _token_for(operator_user)
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            response = await client.get(
                f"/api/v1/cameras/{tenant2_camera.id}"
            )

        assert response.status_code == 404, (
            f"Expected 404 for cross-tenant access, got {response.status_code}"
        )

    @pytest.mark.asyncio
    async def test_tenant2_cannot_delete_tenant1_camera(
        self,
        app_with_db,
        tenant2_user,
        tenant1_camera,
    ):
        """RED: tenant-2 user should not delete tenant-1 camera."""
        token = _token_for(tenant2_user)
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            response = await client.delete(
                f"/api/v1/cameras/{tenant1_camera.id}"
            )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_tenant1_can_see_own_camera(
        self,
        app_with_db,
        operator_user,
        tenant1_camera,
    ):
        """GREEN: tenant-1 user must still see their own camera."""
        token = _token_for(operator_user)
        async with AsyncClient(
            transport=ASGITransport(app=app_with_db),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            response = await client.get(
                f"/api/v1/cameras/{tenant1_camera.id}"
            )

        assert response.status_code == 200
        assert response.json()["id"] == tenant1_camera.id
