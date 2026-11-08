"""Values the API must refuse, found by testing both ends of every parameter."""

from __future__ import annotations

import uuid
from datetime import timedelta

import numpy as np
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import delete

from app.models import RoiZoneRecord
from app.schemas import CameraCreate, CameraUpdate
from app.services.auth import create_access_token
from app.services.risk_engine import RiskEngine
from app.services.zone_store import zone_store
from argus_vision.engine import OpenVINODetector, usable_bgr
from argus_vision.geometry import CameraCalibration, CameraGeometry, homography_problem
from argus_vision.schemas import CameraCalibrationSchema

TRIANGLE = [{"x": 0.1, "y": 0.1}, {"x": 0.5, "y": 0.1}, {"x": 0.5, "y": 0.5}]
SQUARE_IMAGE = [(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)]
SQUARE_WORLD = [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)]
COLLINEAR = [(0.1, 0.1), (0.2, 0.2), (0.3, 0.3), (0.4, 0.4)]
WINDOW = {"start": "22:00", "end": "06:00", "days": [0, 1]}


@pytest_asyncio.fixture(autouse=True)
async def _clean_zones(test_session_factory):
    async def wipe() -> None:
        async with test_session_factory() as session:
            await session.execute(delete(RoiZoneRecord))
            await session.commit()
        await zone_store.load_all(test_session_factory)

    await wipe()
    yield
    await wipe()


def _client(app, user) -> AsyncClient:
    token = create_access_token(
        {"sub": user.username, "uid": user.id, "role": user.role, "tenant_id": user.tenant_id},
        expires_delta=timedelta(minutes=5),
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers={"Authorization": f"Bearer {token}"})


def _zone(**overrides) -> dict:
    return {"name": "Yard", "points": TRIANGLE, **overrides}


# -- Zones ------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(_zone(name=""), id="empty-name"),
        pytest.param(_zone(name="   "), id="blank-name"),
        pytest.param(_zone(points=[{"x": i / 300, "y": (i % 2) / 2} for i in range(300)]), id="too-many-points"),
        pytest.param(_zone(points=[{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}, {"x": 0.3, "y": 0.3}]), id="zero-area"),
        pytest.param(_zone(points=[{"x": 0.5, "y": 0.5}] * 3), id="identical-points"),
        pytest.param(_zone(threshold_sec=1e300), id="huge-threshold"),
        pytest.param(_zone(threshold_sec=86401), id="threshold-over-a-day"),
        pytest.param(_zone(color=[1, 2]), id="short-colour"),
        pytest.param(_zone(color=[1, 2, 3, 4]), id="long-colour"),
        pytest.param(_zone(color=[300, 0, 0]), id="colour-over-255"),
        pytest.param(_zone(color=[-1, 0, 0]), id="negative-colour"),
        pytest.param(_zone(armed_schedule={"mode": "schedule", "windows": []}), id="schedule-no-windows"),
        pytest.param(
            _zone(armed_schedule={"mode": "schedule", "windows": [{**WINDOW, "days": []}]}), id="schedule-no-days"
        ),
        pytest.param(
            _zone(armed_schedule={"mode": "schedule", "windows": [{**WINDOW, "end": "22:00"}]}),
            id="schedule-start-equals-end",
        ),
        pytest.param(_zone(armed_schedule={"mode": "always", "tz": "Mars/Base"}), id="unknown-timezone"),
        pytest.param(_zone(armed_schedule={"mode": "always", "tz": "../../etc/passwd"}), id="path-timezone"),
    ],
)
async def test_bad_zones_are_refused(app_with_db, admin_user, db_session, bad):
    await db_session.commit()
    async with _client(app_with_db, admin_user) as client:
        assert (await client.post("/api/v1/zones/", json=bad)).status_code == 422


@pytest.mark.parametrize(
    "good",
    [
        pytest.param(_zone(), id="defaults"),
        pytest.param(_zone(threshold_sec=86400), id="threshold-one-day"),
        pytest.param(_zone(color=[0, 255, 128]), id="colour-at-limits"),
        pytest.param(_zone(armed_schedule={"mode": "schedule", "windows": [WINDOW], "tz": "Asia/Kolkata"}), id="schedule"),
        pytest.param(_zone(armed_schedule={"mode": "never", "windows": []}), id="never"),
    ],
)
async def test_valid_zones_are_still_accepted(app_with_db, admin_user, db_session, good):
    await db_session.commit()
    async with _client(app_with_db, admin_user) as client:
        assert (await client.post("/api/v1/zones/", json=good)).status_code == 201


@pytest.mark.parametrize("method", ["delete", "put"])
async def test_a_zone_id_too_large_for_the_database_is_refused(app_with_db, admin_user, db_session, method):
    await db_session.commit()
    async with _client(app_with_db, admin_user) as client:
        kwargs = {"json": _zone()} if method == "put" else {}
        response = await getattr(client, method)("/api/v1/zones/99999999999999999999", **kwargs)
    assert response.status_code == 422


# -- Cameras ----------------------------------------------------------------

CAMERA = {"name": "Gate", "location": "North", "stream_url": "video://a.mp4"}


@pytest.mark.parametrize(
    "bad",
    [
        {"name": ""},
        {"name": "   "},
        {"fps": -5},
        {"fps": 0},
        {"fps": 100000},
        {"resolution": "banana"},
        {"resolution": "0x0"},
        {"resolution": "100000x100000"},
        {"resolution": "640"},
    ],
)
def test_bad_camera_values_are_refused_on_create_and_update(bad):
    with pytest.raises(ValidationError):
        CameraCreate(**{**CAMERA, **bad})
    with pytest.raises(ValidationError):
        CameraUpdate(**bad)


@pytest.mark.parametrize("field,value", [("name", "x" * 256), ("location", "x" * 501), ("stream_url", "x" * 1001)])
def test_update_has_the_same_length_limits_as_create(field, value):
    with pytest.raises(ValidationError):
        CameraUpdate(**{field: value})


@pytest.mark.parametrize("resolution", ["1280x720", "7680x4320", "640X480"])
def test_real_resolutions_are_accepted(resolution):
    assert CameraCreate(**CAMERA, resolution=resolution).resolution == resolution.lower()


# -- Calibration ------------------------------------------------------------


def _calibration(image, world) -> dict:
    return {"homography_image_points": image, "homography_world_points": world}


@pytest.mark.parametrize(
    ("image", "world"),
    [
        pytest.param(COLLINEAR, SQUARE_WORLD, id="image-collinear"),
        pytest.param([(0.5, 0.5)] * 4, SQUARE_WORLD, id="image-identical"),
        pytest.param(SQUARE_IMAGE, [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)], id="world-collinear"),
        pytest.param(SQUARE_IMAGE, [(1.0, 1.0)] * 4, id="world-identical"),
    ],
)
def test_degenerate_calibration_is_refused_and_ignored_at_runtime(image, world):
    assert homography_problem(image, world)
    with pytest.raises(ValidationError):
        CameraCalibrationSchema(**_calibration(image, world))
    # Rows saved before this check existed must not report a working plane.
    geometry = CameraGeometry(1000, 1000, CameraCalibration.from_dict(_calibration(image, world)))
    assert geometry.has_ground_plane is False
    assert geometry.camera_pose is None


def test_a_sound_calibration_is_accepted():
    assert homography_problem(SQUARE_IMAGE, SQUARE_WORLD) is None
    assert CameraCalibrationSchema(**_calibration(SQUARE_IMAGE, SQUARE_WORLD))


def test_a_grid_with_collinear_rows_is_accepted():
    grid = [(x / 4, y / 4) for x in (1, 2, 3) for y in (1, 2, 3)]
    world = [(x * 2.0, y * 2.0) for x in (1, 2, 3) for y in (1, 2, 3)]
    assert homography_problem(grid, world) is None


def test_a_point_above_the_horizon_has_no_ground_position():
    calibration = CameraCalibration(
        homography_image_points=[[0.40, 0.90], [0.60, 0.90], [0.55, 0.60], [0.45, 0.60]],
        homography_world_points=[[0.0, 0.0], [2.5, 0.0], [2.5, 5.0], [0.0, 5.0]],
    )
    geometry = CameraGeometry(1000, 1000, calibration)
    assert geometry.foot_to_ground(500, 800) is not None
    assert geometry.foot_to_ground(500, 10) is None


def test_an_impossible_speed_is_withheld():
    context = type("Ctx", (), {"ground_points": [(0.0, 0.0, 0.0), (1.0, 623.0, 0.0)]})
    assert RiskEngine._ground_speed(context) is None
    context.ground_points = [(0.0, 0.0, 0.0), (1.0, 3.0, 0.0)]
    assert RiskEngine._ground_speed(context) == 3.0


# -- Query parameters -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/alerts/?limit=0",
        "/api/v1/alerts/?limit=-1",
        "/api/v1/detections/?limit=0",
        "/api/v1/detections/?limit=-1",
        "/api/v1/detections/?offset=-1",
        "/api/v1/detections/count?hours=-1",
        "/api/v1/detections/count?hours=0",
        "/api/v1/intents/events?limit=0",
        "/api/v1/intents/events?limit=-1",
        "/api/v1/intents/events?hours=-1",
        "/api/v1/intents/distribution?hours=0",
        "/api/v1/analytics/detections/timeline?hours=-1",
        "/api/v1/analytics/detections/timeline?hours=0",
        "/api/v1/analytics/detections/timeline?interval_minutes=-5",
        "/api/v1/analytics/detections/timeline?interval_minutes=0",
        "/api/v1/analytics/detections/heatmap?hours=-1",
        "/api/v1/analytics/detections/class-distribution?hours=0",
    ],
)
async def test_nonsense_query_values_are_refused(app_with_db, admin_user, db_session, url):
    await db_session.commit()
    async with _client(app_with_db, admin_user) as client:
        assert (await client.get(url)).status_code == 422


@pytest.mark.parametrize(
    "url", ["/api/v1/alerts/?limit=1", "/api/v1/detections/?limit=1", "/api/v1/intents/events?limit=1&hours=1"]
)
async def test_smallest_valid_query_values_work(app_with_db, admin_user, db_session, url):
    await db_session.commit()
    async with _client(app_with_db, admin_user) as client:
        assert (await client.get(url)).status_code == 200


@pytest.mark.parametrize("path", ["grants/track", "vehicles/register"])
async def test_a_track_id_too_large_to_exist_is_refused(app_with_db, admin_user, db_session, path):
    await db_session.commit()
    body = {"camera_id": str(uuid.uuid4()), "track_id": 2**70, "plate_text": "KA01AB1234"}
    async with _client(app_with_db, admin_user) as client:
        assert (await client.post(f"/api/v1/security/{path}", json=body)).status_code == 422


# -- Detector input ---------------------------------------------------------


def test_usable_bgr_converts_or_refuses_odd_frames():
    assert usable_bgr(None) is None
    assert usable_bgr(np.zeros((0, 0, 3), dtype=np.uint8)) is None
    assert usable_bgr(np.zeros((10, 10), dtype=np.uint8)).shape == (10, 10, 3)
    assert usable_bgr(np.zeros((10, 10, 1), dtype=np.uint8)).shape == (10, 10, 3)
    assert usable_bgr(np.zeros((10, 10, 4), dtype=np.uint8)).shape == (10, 10, 3)
    bgr = np.zeros((10, 10, 3), dtype=np.uint8)
    assert usable_bgr(bgr) is bgr


def test_a_batch_with_an_unusable_frame_keeps_its_good_neighbours():
    detector = OpenVINODetector.__new__(OpenVINODetector)
    good = np.zeros((10, 10, 3), dtype=np.uint8)
    detector.detect = lambda frame: [{"seen": frame.shape}]
    assert detector.detect_batch([good, np.zeros((0, 0, 3), dtype=np.uint8)]) == [[{"seen": (10, 10, 3)}], []]
