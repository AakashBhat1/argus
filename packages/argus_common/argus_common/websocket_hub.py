"""Tenant-scoped WebSocket fan-out.

Every socket belongs to one tenant and one channel ("global", "alerts", a
camera id, ...). Broadcasts go only to the tenant they concern.

A dashboard on a slow or stalled connection must not hold up everyone else:
messages go to all recipients concurrently, each send is bounded by a
timeout, and a client that cannot keep up is dropped (it reconnects and
resynchronises). Recipient lists are snapshotted before sending, so sockets
connecting or leaving mid-broadcast cannot corrupt the iteration.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Iterable, Optional

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)

GLOBAL_CHANNEL = "global"
ALERTS_CHANNEL = "alerts"
# RFC 6455 "try again later": over capacity or too slow to keep up.
TRY_AGAIN_LATER = 1013


class WebSocketHub:
    def __init__(self, send_timeout: float = 2.0, max_connections_per_tenant: int = 200) -> None:
        self.send_timeout = send_timeout
        self.max_connections_per_tenant = max_connections_per_tenant
        # tenant_id -> channel -> sockets
        self._sockets: dict[str, dict[str, set[WebSocket]]] = {}

    # -- membership ---------------------------------------------------------

    def tenant_connection_count(self, tenant_id: str) -> int:
        return sum(len(sockets) for sockets in self._sockets.get(tenant_id, {}).values())

    @property
    def connection_count(self) -> int:
        return sum(self.tenant_connection_count(tenant_id) for tenant_id in self._sockets)

    async def connect(
        self,
        websocket: WebSocket,
        channel: str = GLOBAL_CHANNEL,
        tenant_id: str = "1",
        subprotocol: Optional[str] = None,
    ) -> bool:
        """Accept and register the socket; refuse it when the tenant is full."""
        if self.tenant_connection_count(tenant_id) >= self.max_connections_per_tenant:
            logger.warning("WebSocket refused: tenant %s has %d connections", tenant_id, self.max_connections_per_tenant)
            await websocket.close(code=TRY_AGAIN_LATER)
            return False
        await websocket.accept(subprotocol=subprotocol)
        self._sockets.setdefault(tenant_id, {}).setdefault(channel, set()).add(websocket)
        logger.info("WebSocket connected: tenant_id=%s channel=%s", tenant_id, channel)
        return True

    async def disconnect(self, websocket: WebSocket, channel: str = GLOBAL_CHANNEL, tenant_id: str = "1") -> None:
        self._remove(tenant_id, channel, websocket)

    def _remove(self, tenant_id: str, channel: str, websocket: WebSocket) -> None:
        channels = self._sockets.get(tenant_id)
        if not channels:
            return
        sockets = channels.get(channel)
        if sockets is not None:
            sockets.discard(websocket)
            if not sockets:
                del channels[channel]
        if not channels:
            del self._sockets[tenant_id]

    # -- broadcasting -------------------------------------------------------

    async def broadcast_detections(self, camera_id: str, data: dict, tenant_id: str = "1") -> None:
        message = json.dumps({"type": "detections", "data": data})
        await self._send(tenant_id, (camera_id, GLOBAL_CHANNEL), message)

    async def broadcast_alert(self, data: dict, tenant_id: str = "1") -> None:
        message = json.dumps({"type": "alert", "data": data})
        await self._send(tenant_id, (ALERTS_CHANNEL, GLOBAL_CHANNEL), message)

    async def broadcast_security(self, data: dict, tenant_id: str = "1") -> None:
        """Security-state changes (arm mode, grants): alerts channel + global."""
        message = json.dumps({"type": "security", "data": data})
        await self._send(tenant_id, (ALERTS_CHANNEL, GLOBAL_CHANNEL), message)

    async def broadcast_to_channel(self, tenant_id: str, channel: str, data: dict) -> None:
        """Send a JSON payload to one channel of one tenant."""
        await self._send(tenant_id, (channel,), json.dumps(data))

    async def _send(self, tenant_id: str, channels: Iterable[str], message: str) -> None:
        tenant = self._sockets.get(tenant_id, {})
        targets = [(channel, ws) for channel in dict.fromkeys(channels) for ws in list(tenant.get(channel, ()))]
        if not targets:
            return
        delivered = await asyncio.gather(*(self._send_one(ws, message) for _, ws in targets))
        for (channel, ws), ok in zip(targets, delivered):
            if not ok:
                self._remove(tenant_id, channel, ws)

    async def _send_one(self, websocket: WebSocket, message: str) -> bool:
        try:
            await asyncio.wait_for(websocket.send_text(message), timeout=self.send_timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning("Dropping a WebSocket client that did not accept a message within %.1fs", self.send_timeout)
            await self._close_quietly(websocket, TRY_AGAIN_LATER)
            return False
        except Exception as exc:  # disconnected mid-send, closed transport, ...
            logger.debug("WebSocket send failed: %s", exc)
            return False

    async def _close_quietly(self, websocket: WebSocket, code: int) -> None:
        try:
            await asyncio.wait_for(websocket.close(code=code), timeout=self.send_timeout)
        except Exception:
            pass
