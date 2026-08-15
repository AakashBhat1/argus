'''Unit tests for deterministic parking anomaly rules.'''

import inspect
from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid

import numpy as np
import pytest

from app.models import Camera, ParkingSpace
from app.services import parking_anomaly
from app.services.parking_anomaly import ParkingAnomalyDetector
from app.services.parking_occupancy import SlotGeometry, SlotTransition
from app.services.trajectory import TrajectoryFeatures


def _settings(**overrides):
    values = {
        'PARKING_ANOMALY_ENABLED': True,
        'PARKING_ANOMALY_COOLDOWN_SEC': 300.0,
        'PARKING_GHOST_OCCUPANCY_MIN': 10.0,
        'PARKING_GHOST_PLATE_LOOKBACK_MIN': 15.0,
        'PARKING_LOITER_MIN_SEC': 45.0,
        'PARKING_CAR_HOP_MIN_SLOTS': 3,
        'PARKING_CAR_HOP_MIN_STATIONARY': 0.35,
        'PARKING_CHURN_THRESHOLD': 3,
        'PARKING_CHURN_WINDOW_MIN': 15.0,
        'PARKING_QUIET_HOURS_START': 22,
        'PARKING_QUIET_HOURS_END': 6,
        'PARKING_QUIET_HOURS_TZ': 'UTC',
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _features(**overrides):
    values = {
        'object_id': 42,
        'class_label': 'person',
        'duration_sec': 60.0,
        'total_distance': 50.0,
        'avg_speed': 1.0,
        'max_speed': 3.0,
        'direction_changes': 4,
        'stationary_ratio': 0.6,
        'bbox_coverage': 0.1,
        'entry_point': [0.1, 0.5],
        'exit_point': [0.9, 0.5],
        'roi_zones_visited': [],
        'had_intrusion': False,
        'trajectory_points': [[0.1, 0.5], [0.4, 0.5], [0.8, 0.5]],
        'point_count': 3,
    }
    values.update(overrides)
    return TrajectoryFeatures(**values)


def _transition(index: int) -> SlotTransition:
    return SlotTransition(
        space_id=f'P-{index}',
        db_id=f'db-{index}',
        occupied=bool(index % 2),
        score=0.4,
        source='vision',
    )


def test_lane_loitering_rule():
    detector = ParkingAnomalyDetector(_settings())
    alerts = detector.detect_lane_loitering(
        camera_id='cam',
        tenant_id='tenant',
        features=_features(),
        intent_type='loitering',
        now=datetime(2026, 8, 14, 12, 0),
    )
    assert len(alerts) == 1
    assert alerts[0].type == 'lane_loitering'
    assert alerts[0].severity == 'medium'
    assert alerts[0].metadata_['source'] == 'parking_anomaly'


def test_track_cooldown_is_scoped_by_tenant_and_camera():
    detector = ParkingAnomalyDetector(_settings())
    now = datetime(2026, 8, 14, 12, 0)
    common = {
        'features': _features(),
        'intent_type': 'loitering',
        'now': now,
    }
    assert detector.detect_lane_loitering(
        camera_id='cam-a', tenant_id='tenant-a', **common
    )
    assert detector.detect_lane_loitering(
        camera_id='cam-b', tenant_id='tenant-b', **common
    )
    assert detector.detect_lane_loitering(
        camera_id='cam-a', tenant_id='tenant-a', **common
    ) == []


def test_car_hopping_intersects_three_distinct_slots():
    detector = ParkingAnomalyDetector(_settings())
    slots = [
        SlotGeometry(
            f'P-{index}',
            f'db-{index}',
            np.array(
                [
                    [index / 3, 0.0],
                    [(index + 1) / 3, 0.0],
                    [(index + 1) / 3, 1.0],
                    [index / 3, 1.0],
                ],
                np.float32,
            ),
        )
        for index in range(3)
    ]
    alerts = detector.detect_car_hopping(
        camera_id='cam',
        tenant_id='tenant',
        features=_features(),
        slots=slots,
        frame_shape=(100, 100, 3),
        now=datetime(2026, 8, 14, 12, 0),
    )
    assert len(alerts) == 1
    assert alerts[0].type == 'car_hopping'
    assert alerts[0].severity == 'high'


@pytest.mark.parametrize(
    ('hour', 'expected'),
    [(23, True), (3, True), (12, False)],
)
def test_quiet_hours_wraps_midnight(hour, expected):
    detector = ParkingAnomalyDetector(_settings())
    assert detector.is_quiet_hours(datetime(2026, 8, 14, hour, 0)) is expected


def test_after_hours_churn_and_cooldown_suppression():
    detector = ParkingAnomalyDetector(_settings())
    now = datetime(2026, 8, 14, 23, 0)
    alerts = detector.detect_after_hours_churn(
        camera_id='cam',
        tenant_id='tenant',
        transitions=[_transition(1), _transition(2), _transition(3)],
        now=now,
    )
    assert len(alerts) == 1
    assert alerts[0].type == 'after_hours_churn'
    suppressed = detector.detect_after_hours_churn(
        camera_id='cam',
        tenant_id='tenant',
        transitions=[_transition(4)],
        now=now + timedelta(seconds=30),
    )
    assert suppressed == []


@pytest.mark.asyncio
async def test_ghost_occupancy_without_recent_plate(db_session):
    detector = ParkingAnomalyDetector(_settings())
    suffix = uuid.uuid4().hex
    tenant_id = f'ghost-{suffix}'
    camera_id = f'cam-{suffix}'
    now = datetime(2026, 8, 14, 12, 0)
    camera = Camera(
        id=camera_id,
        name='Ghost lot',
        location='Lot',
        stream_url='0',
        tenant_id=tenant_id,
        role='parking',
    )
    space = ParkingSpace(
        id=f'space-{suffix}',
        tenant_id=tenant_id,
        camera_id=camera_id,
        space_id='P-01',
        polygon=[[0, 0], [1, 0], [1, 1], [0, 1]],
        is_occupied=True,
        last_state_change=now - timedelta(minutes=11),
    )
    db_session.add_all([camera, space])
    await db_session.flush()
    alerts = await detector.detect_ghost_occupancy(
        db_session,
        camera_id=camera_id,
        tenant_id=tenant_id,
        now=now,
    )
    assert len(alerts) == 1
    assert alerts[0].type == 'ghost_occupancy'
    assert (
        await detector.detect_ghost_occupancy(
            db_session,
            camera_id=camera_id,
            tenant_id=tenant_id,
            now=now + timedelta(seconds=30),
        )
        == []
    )
    await db_session.delete(space)
    await db_session.delete(camera)
    await db_session.flush()


def test_anomaly_module_has_no_model_dependency():
    source = inspect.getsource(parking_anomaly)
    assert 'torch' not in source
    assert 'transformers' not in source
    assert 'huggingface' not in source
