"""smart-parking-system real-time WebSocket routing & isolation tests."""

from __future__ import annotations

import pytest
from datetime import timedelta
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.models import User
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


class TestParkingWebSocket:

    @pytest.mark.asyncio
    async def test_non_camera_channels_includes_parking(self):
        """Guard the regression: verify "parking" is registered as a non-camera channel."""
        from app.main import NON_CAMERA_CHANNELS
        assert "parking" in NON_CAMERA_CHANNELS

    @pytest.mark.asyncio
    async def test_unknown_channel_is_rejected_4001(self, app_with_db, admin_user):
        """Verify that an unknown channel not in the whitelist is closed with 4001."""
        client = TestClient(app_with_db)
        token = _token_for(admin_user)
        
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(f"/ws/not-a-camera?token={token}") as ws:
                ws.receive()
                
        assert exc_info.value.code == 4001

    @pytest.mark.asyncio
    async def test_parking_channel_tenant_isolation(
        self, app_with_db, admin_user, tenant2_user, db_session
    ):
        """Verify real-time channel connects successfully and enforces tenant isolation."""
        await db_session.commit() # Ensure everything is committed
        
        client = TestClient(app_with_db)
        token_t1 = _token_for(admin_user)      # tenant-1
        token_t2 = _token_for(tenant2_user)    # tenant-2
        
        from app.services.websocket_manager import ws_manager
        
        with client.websocket_connect(f"/ws/parking?token={token_t1}") as ws_t1:
            with client.websocket_connect(f"/ws/parking?token={token_t2}") as ws_t2:
                # 1. Trigger a broadcast for tenant-1
                payload = {
                    "type": "parking",
                    "data": {
                        "event": "exit",
                        "space_id": "G-01",
                        "plate_text": "MH12AB1234",
                        "duration_minutes": 10,
                        "amount_paid": 10.0,
                    }
                }
                
                await ws_manager.broadcast_to_channel("tenant-1", "parking", payload)
                
                # 2. Assert tenant-1 subscriber receives the broadcast
                msg_t1 = ws_t1.receive_json()
                assert msg_t1["type"] == "parking"
                assert msg_t1["data"]["event"] == "exit"
                assert msg_t1["data"]["space_id"] == "G-01"
                assert msg_t1["data"]["plate_text"] == "MH12AB1234"
                
                # 3. Send a distinct tenant-2 event. If tenant-1 leaked, it would
                # be the first message received and this public-surface check fails.
                tenant_2_payload = {
                    "type": "parking",
                    "data": {
                        "event": "entry",
                        "space_id": "F2-08",
                        "plate_text": "DL01AB4321",
                    },
                }
                await ws_manager.broadcast_to_channel(
                    "tenant-2", "parking", tenant_2_payload
                )

                msg_t2 = ws_t2.receive_json()
                assert msg_t2 == tenant_2_payload
