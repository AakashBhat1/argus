"""Fan-out isolation: tenants, channels, slow and dead clients."""

from __future__ import annotations

import asyncio
import json

from argus_common.websocket_hub import TRY_AGAIN_LATER, WebSocketHub


class FakeSocket:
    def __init__(self, delay: float = 0.0, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail
        self.received: list[dict] = []
        self.accepted_with = "unset"
        self.closed_with: int | None = None

    async def accept(self, subprotocol=None):
        self.accepted_with = subprotocol

    async def send_text(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("connection closed")
        await asyncio.sleep(self.delay)
        self.received.append(json.loads(text))

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


async def test_messages_stay_within_tenant_and_channel():
    hub = WebSocketHub()
    cam_a, global_a, alerts_a, global_b = FakeSocket(), FakeSocket(), FakeSocket(), FakeSocket()
    await hub.connect(cam_a, "cam-1", tenant_id="a")
    await hub.connect(global_a, "global", tenant_id="a")
    await hub.connect(alerts_a, "alerts", tenant_id="a")
    await hub.connect(global_b, "global", tenant_id="b")

    await hub.broadcast_detections("cam-1", {"n": 1}, tenant_id="a")
    await hub.broadcast_alert({"id": "x"}, tenant_id="a")

    assert [m["type"] for m in cam_a.received] == ["detections"]
    assert [m["type"] for m in global_a.received] == ["detections", "alert"]
    assert [m["type"] for m in alerts_a.received] == ["alert"]
    assert global_b.received == []


async def test_a_stalled_client_does_not_delay_the_others_and_is_dropped():
    hub = WebSocketHub(send_timeout=0.2)
    stalled, healthy = FakeSocket(delay=30), FakeSocket()
    await hub.connect(stalled, "global", tenant_id="t")
    await hub.connect(healthy, "global", tenant_id="t")

    loop = asyncio.get_running_loop()
    started = loop.time()
    await hub.broadcast_alert({"id": 1}, tenant_id="t")
    assert loop.time() - started < 1.0
    assert healthy.received == [{"type": "alert", "data": {"id": 1}}]
    assert stalled.closed_with == TRY_AGAIN_LATER
    assert hub.connection_count == 1


async def test_dead_clients_are_pruned():
    hub = WebSocketHub()
    dead, live = FakeSocket(fail=True), FakeSocket()
    await hub.connect(dead, "alerts", tenant_id="t")
    await hub.connect(live, "alerts", tenant_id="t")
    await hub.broadcast_security({"mode": "armed"}, tenant_id="t")
    assert hub.connection_count == 1 and live.received[0]["type"] == "security"


async def test_tenant_connection_cap():
    hub = WebSocketHub(max_connections_per_tenant=2)
    sockets = [FakeSocket() for _ in range(3)]
    results = [await hub.connect(ws, "global", tenant_id="t") for ws in sockets]
    assert results == [True, True, False]
    assert sockets[2].closed_with == TRY_AGAIN_LATER and sockets[2].accepted_with == "unset"
    # Other tenants are unaffected.
    assert await hub.connect(FakeSocket(), "global", tenant_id="other")


async def test_disconnect_is_idempotent_and_cleans_up():
    hub = WebSocketHub()
    ws = FakeSocket()
    await hub.connect(ws, "cam-9", tenant_id="t", subprotocol="argus-jwt")
    assert ws.accepted_with == "argus-jwt"
    await hub.disconnect(ws, "cam-9", tenant_id="t")
    await hub.disconnect(ws, "cam-9", tenant_id="t")
    assert hub.connection_count == 0 and hub._sockets == {}
