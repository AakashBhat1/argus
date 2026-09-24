"""Parking accepts the dashboard's session cookie under the same CSRF rules."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from argus_common.web_auth import ACCESS_COOKIE, CSRF_HEADER, WS_AUTH_CLOSE_CODE
from support.identity import token_for

ORIGIN = "https://argus.test"
CAMERA = {"name": "Gate B", "location": "South", "stream_url": "rtsp://203.0.113.50/live", "role": "gate_exit"}


def _browser(app, user):
    client = AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN)
    client.cookies.set(ACCESS_COOKIE, token_for(user), domain="argus.test", path="/")
    return client


async def test_cookie_reads_work(app_with_db, admin_user):
    async with _browser(app_with_db, admin_user) as browser:
        assert (await browser.get("/api/v1/parking/cameras")).status_code == 200


async def test_cookie_writes_need_csrf_header_and_our_origin(app_with_db, admin_user):
    async with _browser(app_with_db, admin_user) as browser:
        assert (await browser.post("/api/v1/parking/cameras", json=CAMERA)).status_code == 403
        evil = {CSRF_HEADER: "1", "origin": "https://evil.example"}
        assert (await browser.post("/api/v1/parking/cameras", json=CAMERA, headers=evil)).status_code == 403
        ours = {CSRF_HEADER: "1", "origin": ORIGIN}
        assert (await browser.post("/api/v1/parking/cameras", json=CAMERA, headers=ours)).status_code == 201


async def test_websocket_cookie_from_a_foreign_origin_is_refused(app_with_db, admin_user):
    client = TestClient(app_with_db)
    ours = {"cookie": f"{ACCESS_COOKIE}={token_for(admin_user)}", "origin": ORIGIN, "host": "argus.test"}
    with client.websocket_connect("/ws/parking", headers=ours) as ws:
        ws.send_text("ping")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/parking", headers={**ours, "origin": "https://evil.example"}) as ws:
            ws.receive_text()
    assert exc.value.code == WS_AUTH_CLOSE_CODE
