"""Live WebSocket payload contract for surveillance cameras."""

from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import AsyncMock

import numpy as np

from app.models import Camera
from app.services.stream_manager import VideoStream
from app.services.websocket_manager import ws_manager


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


async def _run_one_frame(stream: VideoStream, monkeypatch) -> dict:
    frame = np.full((360, 640, 3), 90, dtype=np.uint8)
    monkeypatch.setattr("app.services.stream_manager._open_capture", lambda _url: _OneFrameCapture(frame))

    async def stop_after_broadcast(*_args, **_kwargs):
        stream._running = False

    monkeypatch.setattr(ws_manager, "broadcast_detections", AsyncMock(side_effect=stop_after_broadcast))
    stream._store_detections = AsyncMock()
    stream._check_alerts = AsyncMock()
    stream._current_frame_skip = 1
    stream._running = True
    stream._start_time = time.time()
    await stream._process_loop()
    await asyncio.sleep(0)
    return ws_manager.broadcast_detections.await_args.args[1]


def _camera(url: str) -> Camera:
    unique = uuid.uuid4().hex
    return Camera(id=f"cam-{unique}", name="Perimeter", location="North", stream_url=url, tenant_id=f"t-{unique}")


async def test_network_stream_broadcasts_metadata_without_base64_frame(mock_inference_pool, monkeypatch):
    stream = VideoStream(_camera("rtsp://203.0.113.20/live"), mock_inference_pool)
    payload = await _run_one_frame(stream, monkeypatch)
    assert payload["media_transport"] == "webrtc"
    assert "frame_image" not in payload
    assert "parking_slots" not in payload


async def test_local_file_stream_carries_jpeg_frame(mock_inference_pool, monkeypatch):
    stream = VideoStream(_camera("video://clip.mp4"), mock_inference_pool)
    payload = await _run_one_frame(stream, monkeypatch)
    assert payload["media_transport"] == "websocket_jpeg"
    assert payload["frame_image"]
