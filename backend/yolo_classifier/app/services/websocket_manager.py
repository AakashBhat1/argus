import json
import logging
from fastapi import WebSocket
from typing import Optional

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections segregated by tenant_id for real-time updates."""

    def __init__(self):
        # tenant_id -> list[WebSocket]
        self._global_connections: dict[str, list[WebSocket]] = {}
        self._alert_connections: dict[str, list[WebSocket]] = {}
        # tenant_id -> { channel -> list[WebSocket] }
        self._connections: dict[str, dict[str, list[WebSocket]]] = {}
        self._broadcast_log_count = 0

    async def connect(self, websocket: WebSocket, channel: str = "global", tenant_id: str = "1"):
        await websocket.accept()
        
        # Ensure dict structures exist for tenant
        if tenant_id not in self._global_connections:
            self._global_connections[tenant_id] = []
        if tenant_id not in self._alert_connections:
            self._alert_connections[tenant_id] = []
        if tenant_id not in self._connections:
            self._connections[tenant_id] = {}

        if channel == "alerts":
            self._alert_connections[tenant_id].append(websocket)
        elif channel == "global":
            self._global_connections[tenant_id].append(websocket)
        else:
            if channel not in self._connections[tenant_id]:
                self._connections[tenant_id][channel] = []
            self._connections[tenant_id][channel].append(websocket)

        logger.info(f"WebSocket connected: tenant_id={tenant_id} channel={channel}")

    async def disconnect(self, websocket: WebSocket, channel: str = "global", tenant_id: str = "1"):
        if channel == "alerts":
            if tenant_id in self._alert_connections:
                self._alert_connections[tenant_id] = [
                    ws for ws in self._alert_connections[tenant_id] if ws != websocket
                ]
        elif channel == "global":
            if tenant_id in self._global_connections:
                self._global_connections[tenant_id] = [
                    ws for ws in self._global_connections[tenant_id] if ws != websocket
                ]
        else:
            if tenant_id in self._connections and channel in self._connections[tenant_id]:
                self._connections[tenant_id][channel] = [
                    ws for ws in self._connections[tenant_id][channel] if ws != websocket
                ]

    async def broadcast_detections(self, camera_id: str, data: dict, tenant_id: str = "1"):
        det_count = len(data.get("detections", []))
        has_frame = bool(data.get("frame_image"))
        message = json.dumps({"type": "detections", "data": data})
        msg_kb = len(message) / 1024
        
        global_conns = self._global_connections.get(tenant_id, [])
        if det_count > 0 or self._broadcast_log_count < 5:
            logger.info(
                "WS broadcast: tenant=%s dets=%s frame=%s size=%.1fKB global_clients=%s",
                tenant_id, det_count, has_frame, msg_kb, len(global_conns),
            )
            self._broadcast_log_count += 1
        await self._send_to_channel(tenant_id, camera_id, message)
        await self._send_to_global(tenant_id, message)

    async def broadcast_alert(self, data: dict, tenant_id: str = "1"):
        message = json.dumps({"type": "alert", "data": data})
        dead = []
        alert_conns = self._alert_connections.get(tenant_id, [])
        for ws in alert_conns:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            alert_conns.remove(ws)

        await self._send_to_global(tenant_id, message)

    async def _send_to_channel(self, tenant_id: str, channel: str, message: str):
        if tenant_id not in self._connections or channel not in self._connections[tenant_id]:
            return
        dead = []
        for ws in self._connections[tenant_id][channel]:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[tenant_id][channel].remove(ws)

    async def _send_to_global(self, tenant_id: str, message: str):
        if tenant_id not in self._global_connections:
            return
        dead = []
        for ws in self._global_connections[tenant_id]:
            try:
                await ws.send_text(message)
            except Exception as exc:
                logger.warning(f"WebSocket send failed for tenant {tenant_id}: {exc}")
                dead.append(ws)
        for ws in dead:
            self._global_connections[tenant_id].remove(ws)

    @property
    def connection_count(self) -> int:
        count = 0
        for tenant_id in self._global_connections:
            count += len(self._global_connections[tenant_id])
            count += len(self._alert_connections.get(tenant_id, []))
        for tenant_id in self._connections:
            for conns in self._connections[tenant_id].values():
                count += len(conns)
        return count


ws_manager = WebSocketManager()
