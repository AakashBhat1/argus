import json
import logging
from fastapi import WebSocket
from typing import Optional

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}
        self._alert_connections: list[WebSocket] = []
        self._global_connections: list[WebSocket] = []
        self._broadcast_log_count = 0

    async def connect(self, websocket: WebSocket, channel: str = "global"):
        await websocket.accept()
        if channel == "alerts":
            self._alert_connections.append(websocket)
        elif channel == "global":
            self._global_connections.append(websocket)
        else:
            if channel not in self._connections:
                self._connections[channel] = []
            self._connections[channel].append(websocket)

        logger.info(f"WebSocket connected: channel={channel}")

    async def disconnect(self, websocket: WebSocket, channel: str = "global"):
        if channel == "alerts":
            self._alert_connections = [
                ws for ws in self._alert_connections if ws != websocket
            ]
        elif channel == "global":
            self._global_connections = [
                ws for ws in self._global_connections if ws != websocket
            ]
        else:
            if channel in self._connections:
                self._connections[channel] = [
                    ws for ws in self._connections[channel] if ws != websocket
                ]

    async def broadcast_detections(self, camera_id: str, data: dict):
        det_count = len(data.get("detections", []))
        has_frame = bool(data.get("frame_image"))
        message = json.dumps({"type": "detections", "data": data})
        msg_kb = len(message) / 1024
        if det_count > 0 or self._broadcast_log_count < 5:
            logger.info(
                "WS broadcast: dets=%s frame=%s size=%.1fKB global_clients=%s",
                det_count, has_frame, msg_kb, len(self._global_connections),
            )
            self._broadcast_log_count += 1
        await self._send_to_channel(camera_id, message)
        await self._send_to_global(message)

    async def broadcast_alert(self, data: dict):
        message = json.dumps({"type": "alert", "data": data})
        dead = []
        for ws in self._alert_connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self._alert_connections.remove(ws)

        await self._send_to_global(message)

    async def _send_to_channel(self, channel: str, message: str):
        if channel not in self._connections:
            return
        dead = []
        for ws in self._connections[channel]:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[channel].remove(ws)

    async def _send_to_global(self, message: str):
        dead = []
        for ws in self._global_connections:
            try:
                await ws.send_text(message)
            except Exception as exc:
                logger.warning("WebSocket send failed: %s", exc)
                dead.append(ws)
        for ws in dead:
            self._global_connections.remove(ws)

    @property
    def connection_count(self) -> int:
        count = len(self._global_connections) + len(self._alert_connections)
        for conns in self._connections.values():
            count += len(conns)
        return count


ws_manager = WebSocketManager()
