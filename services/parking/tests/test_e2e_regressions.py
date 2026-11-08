"""Parking behaviour corrected after the end-to-end test of Phases 1-4."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.models import Camera
from app.schemas import CameraCreate, CameraUpdate
from app.services.ocr_service import find_state, is_valid_plate
from app.services.parking_commands import validate_plate
from app.services.stream_manager import ParkingStream, stream_manager
from support.identity import create_access_token

CAMERA = {"name": "Gate", "location": "North", "stream_url": "video://a.mp4"}


def _token(user) -> str:
    return create_access_token(
        {"sub": user.username, "uid": user.id, "role": user.role, "tenant_id": user.tenant_id},
        expires_delta=timedelta(minutes=5),
    )


# -- Plate format -----------------------------------------------------------


@pytest.mark.parametrize(
    "plate",
    ["KA01MX9087", "MH12AB1234", "DL01C1234", "CG04AB1234", "TG09AB1234", "OD02AB1234", "22BH1234AA", "23BH0001C"],
)
def test_real_plates_are_valid(plate):
    assert is_valid_plate(plate)
    assert validate_plate(plate) == (True, plate)


@pytest.mark.parametrize("plate", ["ZZ99ZZ9999", "XX12AB1234", "22BH1234OO", "22BH123AA", "BH221234AA", "1234"])
def test_invented_plates_are_invalid(plate):
    assert not is_valid_plate(plate)
    assert validate_plate(plate)[0] is False


def test_bharat_series_has_its_own_state_label():
    assert find_state("22BH1234AA") == "Bharat series"


# -- Camera schema ----------------------------------------------------------


@pytest.mark.parametrize("bad", [{"name": ""}, {"name": "  "}, {"resolution": "banana"}, {"resolution": "0x0"}])
def test_bad_parking_camera_values_are_refused(bad):
    with pytest.raises(ValidationError):
        CameraCreate(**{**CAMERA, **bad})
    with pytest.raises(ValidationError):
        CameraUpdate(**bad)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(["garbage"], id="strings"),
        pytest.param([1, 2], id="too-short"),
        pytest.param([[0.1, 0.1], [0.2, 0.2]], id="two-points"),
        pytest.param([[0.1, 0.1], [0.2, 0.2], [0.3, 0.3]], id="zero-area"),
        pytest.param([[0.1, 0.1], [0.5, "x"], [0.3, 0.9]], id="non-numeric"),
        pytest.param([[0.1, 0.1], [float("inf"), 0.1], [0.3, 0.9]], id="infinite"),
        pytest.param([[-1, 0.1], [0.5, 0.1], [0.3, 0.9]], id="negative"),
        pytest.param([[i / 200, (i % 2) / 2] for i in range(200)], id="too-many-points"),
    ],
)
def test_garbage_gate_roi_is_refused(bad):
    with pytest.raises(ValidationError):
        CameraCreate(**CAMERA, gate_roi=bad)
    with pytest.raises(ValidationError):
        CameraUpdate(gate_roi=bad)


@pytest.mark.parametrize(
    "good",
    [
        pytest.param(None, id="cleared"),
        pytest.param([[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]], id="normalized"),
        pytest.param([[10, 10], [900, 10], [500, 700]], id="pixels"),
        pytest.param([0.1, 0.1, 0.9, 0.1, 0.5, 0.9], id="flat"),
        pytest.param([{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.5, "y": 0.9}], id="objects"),
    ],
)
def test_valid_gate_roi_formats_are_accepted(good):
    assert CameraUpdate(gate_roi=good).gate_roi == good


# -- Query limits -----------------------------------------------------------


@pytest.mark.parametrize("url", ["/api/v1/parking/plates?limit=0", "/api/v1/parking/plates?limit=-1", "/api/v1/parking/activity?limit=0"])
async def test_nonsense_parking_limits_are_refused(app_with_db, admin_user, db_session, url):
    await db_session.commit()
    async with AsyncClient(transport=ASGITransport(app=app_with_db), base_url="http://t") as client:
        client.headers = {"Authorization": f"Bearer {_token(admin_user)}"}
        assert (await client.get(url)).status_code == 422


# -- Slot preview -----------------------------------------------------------


class _FrameCapture:
    def isOpened(self):
        return True

    def read(self):
        return True, np.zeros((100, 200, 3), dtype=np.uint8)

    def release(self):
        return None


SLOTS = {
    "slots": [
        {"space_id": "L", "polygon": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]},
        {"space_id": "R", "polygon": [[0.5, 0], [1, 0], [1, 1], [0.5, 1]]},
    ]
}


async def _preview(app, user, db_session, monkeypatch, pool) -> dict:
    camera = Camera(name="Lot", location="L", stream_url="0", tenant_id=user.tenant_id, role="parking")
    db_session.add(camera)
    await db_session.commit()
    monkeypatch.setattr("app.routers.parking._open_capture", lambda _url: _FrameCapture())
    monkeypatch.setattr(stream_manager, "_inference_pool", pool)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        client.headers = {"Authorization": f"Bearer {_token(user)}"}
        response = await client.post("/api/v1/parking/slots/preview", json={"camera_id": camera.id, **SLOTS})
    assert response.status_code == 200, response.text
    return response.json()


async def test_preview_reports_a_parked_car_as_occupied(app_with_db, admin_user, db_session, monkeypatch):
    pool = MagicMock()
    car = {"class_label": "car", "confidence": 0.9, "bbox": [5.0, 5.0, 90.0, 90.0]}
    pool.submit = AsyncMock(return_value=[car])
    body = await _preview(app_with_db, admin_user, db_session, monkeypatch, pool)
    assert {slot["space_id"]: slot["occupied"] for slot in body["slots"]} == {"L": True, "R": False}
    assert body["vehicle_detection_ran"] is True


async def test_preview_says_so_when_it_could_not_look_for_vehicles(app_with_db, admin_user, db_session, monkeypatch):
    body = await _preview(app_with_db, admin_user, db_session, monkeypatch, None)
    assert body["vehicle_detection_ran"] is False
    assert all(slot["occupied"] is False for slot in body["slots"])


# -- Stream lifecycle -------------------------------------------------------


class _SlowCapture:
    def __init__(self):
        self.reading = 0
        self.overlap = False
        self.releases = 0
        self.opened = True

    def isOpened(self):
        return self.opened

    def read(self):
        self.reading += 1
        time.sleep(0.05)
        self.reading -= 1
        return True, np.zeros((64, 64, 3), dtype=np.uint8)

    def grab(self):
        return True

    def get(self, _prop):
        return 25.0

    def release(self):
        if self.reading:
            self.overlap = True
        self.releases += 1
        self.opened = False


async def test_stop_waits_for_the_read_in_flight(mock_inference_pool, monkeypatch):
    captures: list[_SlowCapture] = []

    def opener(_url):
        captures.append(_SlowCapture())
        return captures[-1]

    monkeypatch.setattr("app.services.stream_manager._open_capture", opener)
    camera = Camera(id="cam-stop", name="Lot", location="L", stream_url="rtsp://203.0.113.9/x", tenant_id="t", role="gate_entry")
    stream = ParkingStream(camera, mock_inference_pool)
    stream._process_frame = AsyncMock()
    assert await stream.start()
    deadline = time.monotonic() + 3
    while not (len(captures) == 2 and captures[-1].reading):
        assert time.monotonic() < deadline
        await asyncio.sleep(0.005)
    await stream.stop()
    assert captures[-1].overlap is False
    assert captures[-1].releases == 1
