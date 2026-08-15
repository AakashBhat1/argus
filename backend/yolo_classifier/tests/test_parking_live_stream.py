"""Regression guards for the parking live-stream WebSocket contracts."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest

from app.models import Camera, ParkingSpace
from app.services.crime_classifier import crime_classifier
from app.services.intrusion_pipeline import PipelineResult
from app.services.parking_occupancy import SlotGeometry, SlotTransition
from app.services.parking_occupancy_service import (
    apply_occupancy_tick,
    invalidate_slots,
    set_cached_slots,
)
from app.services.roboflow_classifier import roboflow_classifier
from app.services.stream_manager import VideoStream
from app.services.websocket_manager import ws_manager


FIXTURES = Path(__file__).parent / "fixtures" / "parking"
FULL_FRAME = np.array(
    [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    dtype=np.float32,
)


class _OneFrameCapture:
    def __init__(self, frame: np.ndarray):
        self._frame = frame
        self._open = True

    def isOpened(self) -> bool:
        return self._open

    def read(self) -> tuple[bool, np.ndarray]:
        return True, self._frame.copy()

    def release(self) -> None:
        self._open = False


def _real_frame() -> np.ndarray:
    frame = cv2.imread(str(FIXTURES / "frame_065.png"))
    assert frame is not None
    return frame


def _camera(role: str, *, suffix: str | None = None) -> Camera:
    unique = suffix or uuid.uuid4().hex
    return Camera(
        id=f"cam-{role}-{unique}",
        name=f"{role.title()} camera",
        location="Test lot",
        stream_url="video://contract-test.mp4",
        tenant_id=f"tenant-{unique}",
        role=role,
    )


async def _run_one_frame(stream: VideoStream, monkeypatch, frame: np.ndarray) -> None:
    monkeypatch.setattr(
        "app.services.stream_manager._open_capture",
        lambda _stream_url: _OneFrameCapture(frame),
    )

    async def stop_after_broadcast(*_args, **_kwargs) -> None:
        stream._running = False

    monkeypatch.setattr(
        ws_manager,
        "broadcast_detections",
        AsyncMock(side_effect=stop_after_broadcast),
    )
    stream._current_frame_skip = 1
    stream._running = True
    stream._start_time = time.time()
    await stream._process_loop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_parking_frame_broadcast_targets_camera_with_live_payload(
    mock_inference_pool, monkeypatch
):
    frame = _real_frame()
    camera = _camera("parking")
    slot = SlotGeometry("P-01", "space-P-01", FULL_FRAME)
    set_cached_slots(camera.id, camera.tenant_id, [slot])
    stream = VideoStream(camera, mock_inference_pool)
    monkeypatch.setattr(
        "app.services.stream_manager.apply_occupancy_tick", AsyncMock()
    )

    try:
        await _run_one_frame(stream, monkeypatch, frame)
    finally:
        invalidate_slots(camera.id, camera.tenant_id)

    broadcast = ws_manager.broadcast_detections
    broadcast.assert_awaited_once()
    assert broadcast.await_args.args[0] == camera.id
    assert broadcast.await_args.kwargs == {"tenant_id": camera.tenant_id}

    payload = broadcast.await_args.args[1]
    assert payload["camera_id"] == camera.id
    assert payload["frame_image"]
    assert len(payload["parking_slots"]) == 1
    assert {"space_id", "occupied", "score"} <= set(
        payload["parking_slots"][0]
    )


@pytest.mark.asyncio
async def test_parking_transition_message_preserves_published_key_names(
    app_with_db, db_session, monkeypatch
):
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
    transition = SlotTransition(
        space_id=space.space_id,
        db_id=space.id,
        occupied=True,
        score=0.42,
        source="vision",
    )

    await apply_occupancy_tick(camera.id, camera.tenant_id, [transition])

    broadcast.assert_awaited_once()
    tenant_id, channel, message = broadcast.await_args.args
    assert (tenant_id, channel) == (camera.tenant_id, "parking")
    assert message["type"] == "parking"
    assert set(message["data"]) == {"event", "camera_id", "slots"}
    assert message["data"]["event"] == "occupancy"
    assert message["data"]["camera_id"] == camera.id
    assert len(message["data"]["slots"]) == 1
    assert {"space_id", "occupied", "score"} <= set(
        message["data"]["slots"][0]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("camera_role", "class_label", "should_trigger"),
    [
        ("parking", "person", True),
        ("surveillance", "person", False),
        ("parking", "car", False),
    ],
)
async def test_parking_crime_wiring_requires_role_and_person_track(
    camera_role,
    class_label,
    should_trigger,
    mock_inference_pool,
    monkeypatch,
):
    frame = _real_frame()
    camera = _camera(camera_role)
    stream = VideoStream(camera, mock_inference_pool)
    tracked = [{"class_label": class_label}]
    stream._pipeline = SimpleNamespace(
        process=lambda *_args: PipelineResult(
            tracked_objects=tracked,
            intrusion_events=[],
        )
    )
    stream._store_detections = AsyncMock()
    stream._check_alerts = AsyncMock()
    stream._crime_classify = AsyncMock()
    monkeypatch.setattr(crime_classifier, "_enabled", True)
    monkeypatch.setattr(roboflow_classifier, "_enabled", False)

    await _run_one_frame(stream, monkeypatch, frame)

    if should_trigger:
        stream._crime_classify.assert_awaited_once()
        assert stream._crime_classify.await_args.kwargs == {
            "parking_activity": True
        }
    else:
        stream._crime_classify.assert_not_awaited()
