"""
TEST 2 - Authentication Enforcement on API Routes

RED: cameras.py router has NO authentication dependency. Any anonymous
     caller can list, create, update, and delete cameras. Tests expect
     401 Unauthorized without a token.

GREEN: Add Depends(get_current_active_user) to each camera router endpoint
       or at the router level.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


CAMERA_PAYLOAD = {
    "name": "Front Door",
    "location": "Entrance",
    "stream_url": "rtsp://10.0.0.1/live",
    "resolution": "1280x720",
    "fps": 30,
}


class TestAuthEnforcementOnCameraRoutes:
    @pytest.mark.asyncio
    async def test_list_cameras_requires_auth(self, anon_client: AsyncClient):
        """RED: GET /cameras/ without a token must return 401."""
        response = await anon_client.get("/api/v1/cameras/")
        assert response.status_code == 401, (
            f"Expected 401 but got {response.status_code}. "
            "Camera list endpoint has no authentication."
        )

    @pytest.mark.asyncio
    async def test_create_camera_requires_auth(self, anon_client: AsyncClient):
        """RED: POST /cameras/ without a token must return 401."""
        response = await anon_client.post("/api/v1/cameras/", json=CAMERA_PAYLOAD)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_update_camera_requires_auth(self, anon_client: AsyncClient):
        """RED: PUT /cameras/{id} without a token must return 401."""
        response = await anon_client.put(
            "/api/v1/cameras/nonexistent-id",
            json={"name": "hacked"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_camera_requires_auth(self, anon_client: AsyncClient):
        """RED: DELETE /cameras/{id} without a token must return 401."""
        response = await anon_client.delete("/api/v1/cameras/nonexistent-id")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_cameras_succeeds_with_valid_token(
        self, auth_client: AsyncClient
    ):
        """GREEN: Authenticated request must return 200."""
        response = await auth_client.get("/api/v1/cameras/")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self, anon_client: AsyncClient):
        """An expired / tampered token must return 401, not 500."""
        response = await anon_client.get(
            "/api/v1/cameras/",
            headers={"Authorization": "Bearer this.is.not.a.valid.jwt"},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_alerts_require_auth(self, anon_client: AsyncClient):
        """RED: GET /alerts/ without a token must return 401."""
        response = await anon_client.get("/api/v1/alerts/")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_detections_require_auth(self, anon_client: AsyncClient):
        """RED: GET /detections/ without a token must return 401."""
        response = await anon_client.get("/api/v1/detections/")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_streams_start_requires_auth(self, anon_client: AsyncClient):
        """RED: POST /streams/{id}/start without a token must return 401."""
        response = await anon_client.post("/api/v1/streams/fake-id/start")
        assert response.status_code == 401
