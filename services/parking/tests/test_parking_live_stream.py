"""Regression guards for the parking live-stream WebSocket contracts."""

from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import AsyncMock

import numpy as np

from app.models import Camera, ParkingSpace
from app.services.parking_occupancy import SlotGeometry, SlotTransition
from app.services.parking_occupancy_service import (
    apply_occupancy_tick,
    invalidate_slots,
    set_cached_slots,
)
from app.services.stream_manager import ParkingStream
from app.services.websocket_manager import ws_manager

from support import synthetic_media

FULL_FRAME = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float32)


class _OneFrameCapture:
    def __init__(self, frame: np.ndarray):
        self._frame = frame
        self._open = True

    def isOpened(self) -> bool:
        return self._open

    def read(self):
        return True, self._frame.copy()

    def release(self) -> None:
        self._open = False

    def set(self, *_args) -> bool:
        return True


def _frame() -> np.ndarray:
    return synthetic_media.parking_lot_frame({0, 1}, seed=3)


def _camera(role: str, *, suffix: str | None = None, url: str = "video://contract-test.mp4") -> Camera:
    unique = suffix or uuid.uuid4().hex
    return Camera(
        id=f"cam-{role}-{unique}",
        name=f"{role.title()} camera",
        location="Test lot",
        stream_url=url,
        tenant_id=f"tenant-{unique}",
        role=role,
    )


async def _run_one_frame(stream: ParkingStream, monkeypatch, frame: np.ndarray) -> AsyncMock:
    monkeypatch.setattr(
        "app.services.stream_manager._open_capture", lambda _url: _OneFrameCapture(frame)
    )
    broadcast = AsyncMock()

    async def stop_after_broadcast(*args, **kwargs):
        await broadcast(*args, **kwargs)
        stream._running = False

    monkeypatch.setattr(ws_manager, "broadcast_to_channel", AsyncMock(side_effect=stop_after_broadcast))
    stream._frame_skip = 1
    stream._running = True
    stream._start_time = time.time()
    await stream._process_loop()
    await asyncio.sleep(0)
    return broadcast


async def test_lot_frame_broadcast_targets_camera_channel_with_slots(mock_inference_pool, monkeypatch):
    camera = _camera("parking")
    set_cached_slots(camera.id, camera.tenant_id, [SlotGeometry("P-01", "space-P-01", FULL_FRAME)])
    stream = ParkingStream(camera, mock_inference_pool)
    monkeypatch.setattr("app.services.stream_manager.apply_occupancy_tick", AsyncMock())
    try:
        broadcast = await _run_one_frame(stream, monkeypatch, _frame())
    finally:
        invalidate_slots(camera.id, camera.tenant_id)

    broadcast.assert_awaited_once()
    tenant_id, channel, message = broadcast.await_args.args
    assert (tenant_id, channel) == (camera.tenant_id, camera.id)
    assert message["type"] == "detections"
    payload = message["data"]
    assert payload["camera_id"] == camera.id
    assert payload["frame_image"]
    assert payload["media_transport"] == "websocket_jpeg"
    assert len(payload["parking_slots"]) == 1
    assert {"space_id", "occupied", "score"} <= set(payload["parking_slots"][0])


async def test_network_gate_stream_sends_metadata_without_base64_frame(mock_inference_pool, monkeypatch):
    camera = _camera("gate_entry", url="rtsp://203.0.113.20/live")
    stream = ParkingStream(camera, mock_inference_pool)
    broadcast = await _run_one_frame(stream, monkeypatch, _frame())
    payload = broadcast.await_args.args[2]["data"]
    assert payload["media_transport"] == "webrtc"
    assert "frame_image" not in payload
    assert payload["role"] == "gate_entry"


async def test_occupancy_transition_message_keeps_published_key_names(app_with_db, db_session, monkeypatch):
    unique = uuid.uuid4().hex
    camera = _camera("parking", suffix=unique)
    space = ParkingSpace(
        id=f"space-{unique}",
        tenant_id=camera.tenant_id,
        camera_id=camera.id,
        space_id="P-01",
        polygon=FULL_FRAME.tolist(),
        is_occupied=False,
    )
    db_session.add_all([camera, space])
    await db_session.commit()

    broadcast = AsyncMock()
    monkeypatch.setattr(ws_manager, "broadcast_to_channel", broadcast)
    monkeypatch.setattr(ws_manager, "broadcast_alert", AsyncMock())
    transition = SlotTransition(space_id="P-01", db_id=space.id, occupied=True, score=0.42, source="vision")

    await apply_occupancy_tick(camera.id, camera.tenant_id, [transition])

    broadcast.assert_awaited_once()
    tenant_id, channel, message = broadcast.await_args.args
    assert (tenant_id, channel) == (camera.tenant_id, "parking")
    assert message["type"] == "parking"
    assert set(message["data"]) == {"event", "camera_id", "slots"}
    assert message["data"]["event"] == "occupancy"
    assert {"space_id", "occupied", "score"} <= set(message["data"]["slots"][0])
