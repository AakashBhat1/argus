"""
TEST 6 - Stream Reconnection with Circuit Breaker

RED: VideoStream._process_loop() retries with fixed asyncio.sleep(2) -
     no limit on attempts, no exponential backoff, no circuit breaker.

GREEN: Add reconnect_attempt counter with max_reconnect_attempts and
       exponential backoff. After max attempts, set _running = False.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import Camera


def _make_camera() -> Camera:
    cam = MagicMock(spec=Camera)
    cam.id = "cam-reconnect-test"
    cam.name = "Reconnect Cam"
    cam.stream_url = "rtsp://192.0.2.1/broken"
    return cam


class TestStreamReconnection:
    @pytest.mark.asyncio
    async def test_stream_stops_after_max_reconnect_attempts(self):
        """
        RED: With a permanently broken source, the stream must stop
        after MAX_RECONNECT_ATTEMPTS. Currently loops forever.
        """
        from app.services.stream_manager import VideoStream

        camera = _make_camera()
        mock_pool = MagicMock()
        mock_pool.submit = AsyncMock(return_value=[])

        broken_cap = MagicMock()
        broken_cap.isOpened.return_value = True
        broken_cap.read.return_value = (False, None)
        broken_cap.release = MagicMock()

        with patch(
            "app.services.stream_manager._open_capture", return_value=broken_cap
        ):
            stream = VideoStream(camera, mock_pool)
            started = await stream.start()
            assert started is True

            deadline = asyncio.get_event_loop().time() + 10
            while stream._running and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.1)

            assert not stream._running, (
                "Stream should have stopped after exhausting reconnect attempts."
            )
            await stream.stop()

    @pytest.mark.asyncio
    async def test_reconnect_attempt_counter_increments(self):
        """RED: reconnect_attempts counter must exist and increment."""
        from app.services.stream_manager import VideoStream

        camera = _make_camera()
        mock_pool = MagicMock()
        mock_pool.submit = AsyncMock(return_value=[])

        broken_cap = MagicMock()
        broken_cap.isOpened.return_value = True
        broken_cap.read.return_value = (False, None)
        broken_cap.release = MagicMock()

        with patch(
            "app.services.stream_manager._open_capture", return_value=broken_cap
        ):
            stream = VideoStream(camera, mock_pool)
            await stream.start()
            await asyncio.sleep(0.5)

            assert hasattr(stream, "_reconnect_attempts"), (
                "VideoStream must have a _reconnect_attempts counter"
            )
            assert stream._reconnect_attempts >= 1
            await stream.stop()

    @pytest.mark.asyncio
    async def test_successful_reconnect_resets_counter(self):
        """GREEN: When stream recovers, reconnect counter resets to 0."""
        from app.services.stream_manager import VideoStream

        camera = _make_camera()
        mock_pool = MagicMock()
        mock_pool.submit = AsyncMock(return_value=[])

        call_count = {"n": 0}
        fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)

        def variable_read():
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return (False, None)
            return (True, fake_frame)

        cap = MagicMock()
        cap.isOpened.return_value = True
        cap.read.side_effect = variable_read
        cap.release = MagicMock()

        with patch(
            "app.services.stream_manager._open_capture", return_value=cap
        ), patch("app.services.stream_manager.ws_manager") as mock_ws:
            mock_ws.broadcast_detections = AsyncMock()
            stream = VideoStream(camera, mock_pool)
            await stream.start()
            await asyncio.sleep(2.0)

            assert getattr(stream, "_reconnect_attempts", -1) == 0
            await stream.stop()
