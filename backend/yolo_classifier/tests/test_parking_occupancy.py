'''Vision parking occupancy tests, including supplied-video reality checks.'''

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from app.models import (
    Alert,
    AlertSeverity,
    Camera,
    DetectedPlate,
    ParkingActivityLog,
    ParkingSpace,
)
from app.services import parking_service
from app.services.parking_occupancy import (
    OccupancyDebouncer,
    OccupancyDetector,
    SlotGeometry,
    SlotReading,
    SlotTransition,
)
from app.services.stream_manager import _open_capture, _resolve_stream_source
from app.services.parking_occupancy_service import (
    ParkingOccupancyStage,
    apply_occupancy_tick,
    get_cached_slots,
    invalidate_slots,
    load_slots,
    persist_anomaly_alerts,
    set_cached_slots,
    slots_from_rows,
    warm_camera_slots,
)
from app.services.websocket_manager import ws_manager
from app.utils import utc_now

FIXTURES = Path(__file__).parent / 'fixtures' / 'parking'
FULL_FRAME = np.array(
    [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
    dtype=np.float32,
)
LOWER_LEFT_BAY = np.array(
    [[0.0, 0.62], [1 / 13, 0.62], [1 / 13, 0.96], [0.0, 0.96]],
    dtype=np.float32,
)
LOWER_SECOND_BAY = np.array(
    [[1 / 13, 0.62], [2 / 13, 0.62], [2 / 13, 0.96], [1 / 13, 0.96]],
    dtype=np.float32,
)


def _slot(space_id: str = 'P-01', polygon=FULL_FRAME) -> SlotGeometry:
    return SlotGeometry(space_id, f'db-{space_id}', np.asarray(polygon, np.float32))


def _reading(occupied: bool, score: float = 0.5) -> SlotReading:
    return SlotReading('P-01', occupied, score, 'vision', 'db-P-01')


def test_video_uri_resolves_to_repo_clip_and_opens():
    uri = 'video://istockphoto-1370353417-640_adpp_is_slower_8x.mp4'
    resolved = Path(_resolve_stream_source(uri))
    assert resolved.is_file()
    assert resolved.parent.name == 'video'
    capture = _open_capture(uri)
    try:
        assert capture.isOpened()
        ok, frame = capture.read()
        assert ok and frame is not None
        assert frame.shape[:2] == (432, 768)
    finally:
        capture.release()


def test_video_uri_rejects_parent_directory_traversal():
    unsafe = 'video://../info_about_handover_to_other_ai_tools.md'
    assert _resolve_stream_source(unsafe) == unsafe


def test_uniform_grey_is_free_and_checkerboard_is_occupied():
    detector = OccupancyDetector()
    grey = np.full((128, 64, 3), 127, dtype=np.uint8)
    free = detector.score_slots(grey, [_slot()], [])[0]
    assert free.occupied is False
    assert free.score <= 0.10

    checker = np.zeros((128, 64, 3), dtype=np.uint8)
    checker[::8, :] = 255
    checker[:, ::8] = 255
    occupied = detector.score_slots(checker, [_slot()], [])[0]
    assert occupied.occupied is True
    assert occupied.score >= 0.22


def test_perspective_warp_scores_a_rotated_quad(monkeypatch):
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    polygon = np.array(
        [[0.25, 0.10], [0.80, 0.30], [0.65, 0.90], [0.10, 0.70]],
        dtype=np.float32,
    )
    mask = np.zeros((200, 200), dtype=np.uint8)
    pixel_polygon = np.column_stack((polygon[:, 0] * 200, polygon[:, 1] * 200))
    cv2.fillConvexPoly(mask, pixel_polygon.astype(np.int32), 255)
    monkeypatch.setattr(
        OccupancyDetector,
        '_binary_mask',
        staticmethod(lambda _frame: mask),
    )
    reading = OccupancyDetector().score_slots(
        frame, [_slot(polygon=polygon)], []
    )[0]
    assert reading.occupied is True
    assert reading.score > 0.90


def test_yolo_tiebreaker_only_changes_ambiguous_scores(monkeypatch):
    frame = np.zeros((128, 64, 3), dtype=np.uint8)
    car = {
        'class_label': 'car',
        'bbox_x': 0,
        'bbox_y': 0,
        'bbox_w': 64,
        'bbox_h': 128,
    }
    detector = OccupancyDetector(hi=0.22, lo=0.10, iou_min=0.40)

    def score_for_fraction(fraction: float, detections: list[dict]):
        mask = np.zeros((128, 64), dtype=np.uint8)
        mask[:, : int(64 * fraction)] = 255
        monkeypatch.setattr(
            OccupancyDetector,
            '_binary_mask',
            staticmethod(lambda _frame: mask),
        )
        return detector.score_slots(frame, [_slot()], detections)[0]

    assert score_for_fraction(0.15, []).occupied is False
    ambiguous = score_for_fraction(0.15, [car])
    assert ambiguous.occupied is True
    assert ambiguous.source == 'vision_yolo'
    assert score_for_fraction(0.05, [car]).occupied is False
    assert score_for_fraction(0.30, []).occupied is True


def test_debouncer_rejects_flicker_and_emits_one_stable_transition():
    debouncer = OccupancyDebouncer(consecutive_frames=3)
    assert debouncer.update([_reading(True)]) == []
    assert debouncer.update([_reading(False)]) == []
    assert debouncer.update([_reading(True)]) == []

    stable = OccupancyDebouncer(consecutive_frames=3)
    assert stable.update([_reading(True)]) == []
    assert stable.update([_reading(True)]) == []
    transitions = stable.update([_reading(True)])
    assert len(transitions) == 1
    assert transitions[0].occupied is True
    assert stable.update([_reading(True)]) == []


def test_slot_cache_conversion_and_parking_stage(monkeypatch):
    valid_row = SimpleNamespace(
        camera_id='cam-cache',
        polygon=FULL_FRAME.tolist(),
        space_id='P-01',
        id='db-P-01',
    )
    no_camera = SimpleNamespace(
        camera_id=None,
        polygon=FULL_FRAME.tolist(),
        space_id='P-02',
        id='db-P-02',
    )
    invalid = SimpleNamespace(
        camera_id='cam-cache',
        polygon='not-a-polygon',
        space_id='P-03',
        id='db-P-03',
    )
    slots = slots_from_rows([valid_row, no_camera, invalid])
    assert [slot.space_id for slot in slots] == ['P-01']

    set_cached_slots('cam-cache', 'tenant-cache', slots)
    assert get_cached_slots('cam-cache', 'tenant-cache') == slots
    monkeypatch.setattr(
        'app.services.parking_occupancy_service.time.monotonic',
        iter([10.0, 10.5]).__next__,
    )
    stage = ParkingOccupancyStage('cam-cache', 'tenant-cache')
    checker = np.zeros((128, 64, 3), dtype=np.uint8)
    checker[::8, :] = 255
    checker[:, ::8] = 255
    first = stage.process(checker, [])
    assert len(first.readings) == 1
    assert stage.process(checker, []).readings == []
    assert stage.slots() == slots
    stage.reset()

    invalidate_slots('cam-cache', 'tenant-cache')
    assert get_cached_slots('cam-cache', 'tenant-cache') == []


@pytest.mark.parametrize(
    ('filename', 'polygon', 'expected', 'score_bound'),
    [
        ('frame_010.png', LOWER_LEFT_BAY, False, 0.10),
        ('frame_065.png', LOWER_LEFT_BAY, True, 0.22),
        ('frame_120.png', LOWER_LEFT_BAY, True, 0.22),
        ('frame_010.png', LOWER_SECOND_BAY, True, 0.22),
    ],
)
def test_supplied_clip_hand_labelled_bays(
    filename, polygon, expected, score_bound
):
    frame = cv2.imread(str(FIXTURES / filename))
    assert frame is not None
    reading = OccupancyDetector().score_slots(
        frame, [_slot(polygon=polygon)], []
    )[0]
    assert reading.occupied is expected
    if expected:
        assert reading.score >= score_bound
    else:
        assert reading.score <= score_bound


@pytest.mark.asyncio
async def test_committed_transition_updates_only_ingesting_tenant(
    app_with_db, db_session, monkeypatch
):
    suffix = uuid.uuid4().hex
    camera_t1 = Camera(
        id=f'cam-t1-{suffix}',
        name='Lot one',
        location='A',
        stream_url='0',
        tenant_id=f't1-{suffix}',
        role='parking',
    )
    camera_t2 = Camera(
        id=f'cam-t2-{suffix}',
        name='Lot two',
        location='B',
        stream_url='0',
        tenant_id=f't2-{suffix}',
        role='parking',
    )
    space_t1 = ParkingSpace(
        id=f'space-t1-{suffix}',
        tenant_id=camera_t1.tenant_id,
        camera_id=camera_t1.id,
        space_id='P-01',
        polygon=FULL_FRAME.tolist(),
        is_occupied=False,
    )
    space_t2 = ParkingSpace(
        id=f'space-t2-{suffix}',
        tenant_id=camera_t2.tenant_id,
        camera_id=camera_t2.id,
        space_id='P-01',
        polygon=FULL_FRAME.tolist(),
        is_occupied=False,
    )
    db_session.add_all([camera_t1, camera_t2, space_t1, space_t2])
    await db_session.commit()
    space_t1_id = space_t1.id
    space_t2_id = space_t2.id
    camera_t1_id = camera_t1.id
    tenant_t1_id = camera_t1.tenant_id
    loaded = await load_slots(
        db_session, camera_t1_id, tenant_t1_id, force=True
    )
    assert [slot.space_id for slot in loaded] == ['P-01']
    assert await load_slots(db_session, camera_t1_id, tenant_t1_id) == loaded
    await warm_camera_slots(camera_t1_id, tenant_t1_id)
    broadcast = AsyncMock()
    monkeypatch.setattr(ws_manager, 'broadcast_to_channel', broadcast)
    monkeypatch.setattr(ws_manager, 'broadcast_alert', AsyncMock())

    from app.services.parking_occupancy import SlotTransition

    transition = SlotTransition(
        space_id='P-01',
        db_id=space_t1.id,
        occupied=True,
        score=0.4,
        source='vision',
    )
    await apply_occupancy_tick(
        camera_t1_id,
        tenant_t1_id,
        [transition],
    )
    db_session.expire_all()
    refreshed_t1 = await db_session.get(ParkingSpace, space_t1_id)
    refreshed_t2 = await db_session.get(ParkingSpace, space_t2_id)
    assert refreshed_t1.is_occupied is True
    assert refreshed_t1.entry_time is not None
    assert refreshed_t1.last_state_change is not None
    assert refreshed_t1.detection_source == 'vision'
    assert refreshed_t2.is_occupied is False
    logs = (
        await db_session.execute(
            select(ParkingActivityLog).where(
                ParkingActivityLog.tenant_id == tenant_t1_id
            )
        )
    ).scalars().all()
    assert len(logs) == 1
    broadcast.assert_awaited_once()
    assert broadcast.await_args.args[:2] == (tenant_t1_id, 'parking')

    alert = Alert(
        camera_id=camera_t1_id,
        tenant_id=tenant_t1_id,
        type='parking_lane_loitering',
        severity=AlertSeverity.MEDIUM.value,
        description='Test parking anomaly',
        timestamp=utc_now(),
        metadata_={'rule': 'lane_loitering'},
    )
    await persist_anomaly_alerts([])
    await persist_anomaly_alerts([alert])
    persisted = await db_session.get(Alert, alert.id)
    assert persisted is not None
    ws_manager.broadcast_alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_debounced_vision_vacate_checks_out_gate_session(
    app_with_db, db_session, monkeypatch
):
    suffix = uuid.uuid4().hex
    tenant_id = f'checkout-{suffix}'
    camera = Camera(
        id=f'cam-{suffix}',
        name='Checkout lot',
        location='Lot',
        stream_url='0',
        tenant_id=tenant_id,
        role='parking',
    )
    space = ParkingSpace(
        id=f'space-{suffix}',
        tenant_id=tenant_id,
        camera_id=camera.id,
        space_id='P-01',
        polygon=FULL_FRAME.tolist(),
        is_occupied=False,
    )
    db_session.add_all([camera, space])
    await db_session.commit()

    plate_text = f'MH{suffix[:8].upper()}'
    plate = await parking_service.record_detected_plate(
        db_session,
        tenant_id,
        plate_text,
        confidence=0.99,
        camera_id=camera.id,
    )
    assigned = await parking_service.assign_space(
        db_session, tenant_id, plate_text
    )
    assert assigned is not None
    original_entry = utc_now() - timedelta(minutes=125)
    space.entry_time = original_entry
    await db_session.commit()
    plate_id = plate.id
    space_id = space.id

    debouncer = OccupancyDebouncer(consecutive_frames=5)
    reading = SlotReading('P-01', False, 0.05, 'vision', space_id)
    for _ in range(4):
        assert debouncer.update([reading]) == []
    transitions = debouncer.update([reading])
    assert len(transitions) == 1

    monkeypatch.setattr(ws_manager, 'broadcast_to_channel', AsyncMock())
    monkeypatch.setattr(ws_manager, 'broadcast_alert', AsyncMock())
    await apply_occupancy_tick(camera.id, tenant_id, transitions)

    db_session.expire_all()
    refreshed_space = await db_session.get(ParkingSpace, space_id)
    refreshed_plate = await db_session.get(DetectedPlate, plate_id)
    assert refreshed_space.is_occupied is False
    assert refreshed_space.entry_time is None
    assert refreshed_space.vehicle_id is None
    assert refreshed_plate.is_parked is False
    assert refreshed_plate.exit_time is not None
    assert refreshed_plate.duration_minutes >= 125
    assert refreshed_plate.amount_paid == parking_service.calculate_tariff(125)
    checkout_logs = (
        await db_session.execute(
            select(ParkingActivityLog).where(
                ParkingActivityLog.tenant_id == tenant_id,
                ParkingActivityLog.event_type == 'vision_checkout',
            )
        )
    ).scalars().all()
    assert len(checkout_logs) == 1
    assert checkout_logs[0].space_id == 'P-01'
    assert checkout_logs[0].plate_text == plate_text


@pytest.mark.asyncio
async def test_vision_occupy_does_not_change_gate_occupied_bay(
    app_with_db, db_session, monkeypatch
):
    suffix = uuid.uuid4().hex
    tenant_id = f'occupy-noop-{suffix}'
    camera = Camera(
        id=f'cam-{suffix}',
        name='Occupied lot',
        location='Lot',
        stream_url='0',
        tenant_id=tenant_id,
        role='parking',
    )
    space = ParkingSpace(
        id=f'space-{suffix}',
        tenant_id=tenant_id,
        camera_id=camera.id,
        space_id='P-01',
        polygon=FULL_FRAME.tolist(),
        is_occupied=False,
    )
    db_session.add_all([camera, space])
    await db_session.commit()
    plate_text = f'DL{suffix[:8].upper()}'
    await parking_service.record_detected_plate(
        db_session, tenant_id, plate_text, camera_id=camera.id
    )
    assigned = await parking_service.assign_space(
        db_session, tenant_id, plate_text
    )
    assert assigned is not None
    original_entry = utc_now() - timedelta(minutes=30)
    space.entry_time = original_entry
    await db_session.commit()
    space_pk = space.id
    original_vehicle_id = space.vehicle_id

    debouncer = OccupancyDebouncer(consecutive_frames=5)
    reading = SlotReading('P-01', True, 0.40, 'vision', space_pk)
    transitions = []
    for _ in range(5):
        transitions = debouncer.update([reading])
    assert len(transitions) == 1

    monkeypatch.setattr(ws_manager, 'broadcast_to_channel', AsyncMock())
    monkeypatch.setattr(ws_manager, 'broadcast_alert', AsyncMock())
    await apply_occupancy_tick(camera.id, tenant_id, transitions)

    db_session.expire_all()
    refreshed = await db_session.get(ParkingSpace, space_pk)
    assert refreshed.is_occupied is True
    assert refreshed.vehicle_id == original_vehicle_id
    assert refreshed.entry_time == original_entry
    assert refreshed.detection_source == 'manual'
