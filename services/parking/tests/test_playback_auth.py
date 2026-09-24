"""Edge auth_request hook for WebRTC playback of parking cameras."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from argus_common.web_auth import STREAM_URI_HEADER
from support.identity import User, token_for

URL = "/api/v1/parking/streams/playback-auth"
CAMERA = {"name": "Lot", "location": "L1", "stream_url": "rtsp://203.0.113.60/live", "role": "parking"}


async def test_playback_follows_camera_ownership(app_with_db, admin_user):
    transport = ASGITransport(app=app_with_db)
    headers = {"Authorization": f"Bearer {token_for(admin_user)}"}
    async with AsyncClient(transport=transport, base_url="http://t", headers=headers) as owner:
        camera = (await owner.post("/api/v1/parking/cameras", json=CAMERA)).json()
        uri = {STREAM_URI_HEADER: f"/stream-parking/{camera['id']}/whep"}
        assert (await owner.get(URL, headers=uri)).status_code == 204
        assert (await owner.get(URL, headers={STREAM_URI_HEADER: f"/stream-parking/{camera['id']}/"})).status_code == 403

    stranger = User(username="t9", role="admin", tenant_id="tenant-9")
    async with AsyncClient(transport=transport, base_url="http://t", headers={"Authorization": f"Bearer {token_for(stranger)}"}) as other:
        assert (await other.get(URL, headers=uri)).status_code == 403
    async with AsyncClient(transport=transport, base_url="http://t") as anonymous:
        assert (await anonymous.get(URL, headers=uri)).status_code == 401
