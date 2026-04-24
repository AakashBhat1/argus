"""
TEST 4 - WebSocket Authentication

RED: /ws/{channel} accepts any connection with zero authentication.
     Tests expect a 4001 close code when no valid token is provided.

GREEN: Add token validation to the websocket_endpoint handler via
       ?token= query parameter.
"""

from __future__ import annotations

import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestWebSocketAuthentication:
    @pytest.mark.asyncio
    async def test_ws_connect_without_token_is_rejected(self, app_with_db):
        """
        RED: /ws/global with no token must be closed immediately.
        Currently the connection is accepted.
        """
        from starlette.testclient import TestClient

        client = TestClient(app_with_db, raise_server_exceptions=False)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws/global") as ws:
                data = ws.receive()
                pytest.fail("WebSocket accepted unauthenticated connection")

    @pytest.mark.asyncio
    async def test_ws_connect_with_valid_token_succeeds(
        self, app_with_db, admin_user
    ):
        """GREEN: A valid JWT in ?token= query param must allow connection."""
        from starlette.testclient import TestClient
        from app.services.auth import create_access_token
        from datetime import timedelta

        token = create_access_token(
            data={"sub": admin_user.username, "role": admin_user.role},
            expires_delta=timedelta(minutes=5),
        )
        client = TestClient(app_with_db)
        with client.websocket_connect(f"/ws/global?token={token}") as ws:
            ws.send_text("ping")

    @pytest.mark.asyncio
    async def test_ws_connect_with_expired_token_is_rejected(
        self, app_with_db, admin_user
    ):
        """RED: An expired JWT must cause the server to close the connection."""
        from starlette.testclient import TestClient
        from app.services.auth import create_access_token
        from datetime import timedelta

        expired_token = create_access_token(
            data={"sub": admin_user.username},
            expires_delta=timedelta(minutes=-1),
        )
        client = TestClient(app_with_db, raise_server_exceptions=False)
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/ws/global?token={expired_token}"
            ) as ws:
                ws.receive()
                pytest.fail("Expired token should have been rejected")

    @pytest.mark.asyncio
    async def test_ws_connect_with_tampered_token_is_rejected(self, app_with_db):
        """RED: A JWT signed with a different key must be rejected."""
        from starlette.testclient import TestClient

        bad_token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhdHRhY2tlciJ9.bad_sig"
        client = TestClient(app_with_db, raise_server_exceptions=False)
        with pytest.raises(Exception):
            with client.websocket_connect(
                f"/ws/global?token={bad_token}"
            ) as ws:
                ws.receive()
                pytest.fail("Tampered token should have been rejected")
