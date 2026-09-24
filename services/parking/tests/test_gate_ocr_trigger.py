"""Gate OCR trigger: fires from a worker thread and reports why it is inactive."""

import asyncio
from unittest.mock import patch

import numpy as np
import pytest

from app.services import gate_ocr as gate_ocr_module
from app.services.gate_ocr import GateOcrTrigger, point_in_gate_roi


def _car(track_id: int = 7) -> dict:
    return {
        "object_id": track_id,
        "class_label": "car",
        "bbox_x": 300,
        "bbox_y": 200,
        "bbox_w": 200,
        "bbox_h": 120,
    }


def test_point_in_gate_roi_accepts_normalised_pairs():
    roi = [[0.0, 0.0], [1.0, 0.0], [1.0, 0.5], [0.0, 0.5]]
    assert point_in_gate_roi(640, 100, roi, 1280, 720) is True
    assert point_in_gate_roi(640, 600, roi, 1280, 720) is False


def test_inactive_reason_explains_missing_role_then_missing_polygon():
    trig = GateOcrTrigger("cam-1", "1")
    assert "not loaded" in trig.inactive_reason()
    trig.update_meta("surveillance", None)
    assert "gate_entry" in trig.inactive_reason()
    trig.update_meta("gate_entry", None)
    assert "polygon" in trig.inactive_reason()
    trig.update_meta("gate_entry", [[0, 0], [1, 0], [1, 1], [0, 1]])
    assert trig.inactive_reason() is None
    assert trig.status()["active"] is True


@pytest.mark.asyncio
async def test_process_frame_from_worker_thread_schedules_ocr_on_bound_loop():
    """Regression: the pipeline runs in an executor thread, where
    asyncio.get_running_loop() raises. OCR used to silently never fire."""
    trig = GateOcrTrigger("cam-1", "1")
    loop = asyncio.get_running_loop()
    trig.bind_loop(loop)
    trig.update_meta("gate_entry", [[0, 0], [1, 0], [1, 1], [0, 1]])

    calls: list[np.ndarray] = []

    def fake_recognize(crop):
        calls.append(crop)
        return None, "Unknown", 0.0  # no plate -> no DB write needed

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    with patch.object(gate_ocr_module, "recognize_plate", fake_recognize):
        # Simulate the pipeline's executor thread.
        await loop.run_in_executor(None, trig.process_frame, [_car()], frame)
        # Let the scheduled coroutine run.
        for _ in range(20):
            await asyncio.sleep(0.01)
            if calls:
                break

    assert len(calls) == 1, "OCR was not scheduled from the worker thread"
    assert calls[0].shape == (120, 200, 3)
    status = trig.status()
    assert status["vehicles_in_gate"] == 1
    assert status["plates_read"] == 0
    assert "no readable plate" in status["last_error"]


@pytest.mark.asyncio
async def test_same_track_is_only_ocrd_once():
    trig = GateOcrTrigger("cam-1", "1")
    trig.bind_loop(asyncio.get_running_loop())
    trig.update_meta("gate_entry", [[0, 0], [1, 0], [1, 1], [0, 1]])
    calls = []
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    with patch.object(gate_ocr_module, "recognize_plate", lambda crop: (calls.append(1), None, "Unknown", 0.0)[1:]):
        for _ in range(3):
            trig.process_frame([_car(5)], frame)
            await asyncio.sleep(0.02)
    assert len(calls) == 1


def test_process_frame_without_vehicles_is_a_noop_even_with_no_loop():
    trig = GateOcrTrigger("cam-1", "1")
    trig._loop = None
    trig.update_meta("gate_entry", [[0, 0], [1, 0], [1, 1], [0, 1]])
    trig.process_frame([{"object_id": 1, "class_label": "person", "bbox_x": 0, "bbox_y": 0, "bbox_w": 10, "bbox_h": 10}],
                       np.zeros((10, 10, 3), dtype=np.uint8))
    assert not trig._pending
