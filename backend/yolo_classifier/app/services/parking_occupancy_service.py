'''Async persistence/cache boundary for vision parking occupancy.'''

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.config import get_settings
from app.models import Alert, ParkingActivityLog, ParkingSpace
from app.services import parking_service
from app.services.parking_anomaly import parking_anomaly_detector
from app.services.parking_occupancy import (
    OccupancyDebouncer,
    OccupancyDetector,
    SlotGeometry,
    SlotReading,
    SlotTransition,
)
from app.services.websocket_manager import ws_manager
from app.utils import utc_now

logger = logging.getLogger(__name__)

_slot_cache: dict[tuple[str, str], tuple[SlotGeometry, ...]] = {}
_cache_lock = threading.Lock()


def slots_from_rows(rows) -> list[SlotGeometry]:
    slots = []
    for row in rows:
        if row.camera_id is None or row.polygon is None:
            continue
        try:
            slots.append(
                SlotGeometry(
                    space_id=row.space_id,
                    db_id=row.id,
                    polygon=np.asarray(row.polygon, dtype=np.float32),
                )
            )
        except (TypeError, ValueError):
            logger.warning('Skipping invalid parking polygon for space %s', row.id)
    return slots


def get_cached_slots(camera_id: str, tenant_id: str) -> list[SlotGeometry]:
    with _cache_lock:
        return list(_slot_cache.get((tenant_id, camera_id), ()))


def set_cached_slots(
    camera_id: str, tenant_id: str, slots: list[SlotGeometry]
) -> None:
    with _cache_lock:
        _slot_cache[(tenant_id, camera_id)] = tuple(slots)


def invalidate_slots(camera_id: str, tenant_id: str) -> None:
    with _cache_lock:
        _slot_cache.pop((tenant_id, camera_id), None)


async def load_slots(
    db: AsyncSession,
    camera_id: str,
    tenant_id: str,
    *,
    force: bool = False,
) -> list[SlotGeometry]:
    if not force:
        cached = get_cached_slots(camera_id, tenant_id)
        if cached:
            return cached
    rows = (
        await db.execute(
            select(ParkingSpace)
            .where(
                ParkingSpace.camera_id == camera_id,
                ParkingSpace.tenant_id == tenant_id,
                ParkingSpace.polygon.is_not(None),
            )
            .order_by(ParkingSpace.display_order, ParkingSpace.space_id)
        )
    ).scalars().all()
    slots = slots_from_rows(rows)
    set_cached_slots(camera_id, tenant_id, slots)
    return slots


async def warm_camera_slots(camera_id: str, tenant_id: str) -> None:
    session_factory = database.get_session_factory()
    async with session_factory() as db:
        await load_slots(db, camera_id, tenant_id, force=True)


@dataclass
class ParkingOccupancyTick:
    readings: list[SlotReading]
    transitions: list[SlotTransition]


class ParkingOccupancyStage:
    def __init__(self, camera_id: str, tenant_id: str):
        settings = get_settings()
        self._camera_id = camera_id
        self._tenant_id = tenant_id
        self._enabled = bool(settings.PARKING_OCCUPANCY_ENABLED)
        self._interval = float(settings.PARKING_OCCUPANCY_INTERVAL_SEC)
        self._last_run = 0.0
        self._detector = OccupancyDetector(
            hi=settings.PARKING_OCCUPANCY_HI,
            lo=settings.PARKING_OCCUPANCY_LO,
            iou_min=settings.PARKING_OCCUPANCY_IOU_MIN,
        )
        self._debouncer = OccupancyDebouncer(
            settings.PARKING_OCCUPANCY_DEBOUNCE_FRAMES
        )

    def process(self, frame: np.ndarray, tracked: list[dict]) -> ParkingOccupancyTick:
        now = time.monotonic()
        if not self._enabled or now - self._last_run < self._interval:
            return ParkingOccupancyTick([], [])
        self._last_run = now
        slots = get_cached_slots(self._camera_id, self._tenant_id)
        readings = self._detector.score_slots(frame, slots, tracked)
        return ParkingOccupancyTick(readings, self._debouncer.update(readings))

    def slots(self) -> list[SlotGeometry]:
        return get_cached_slots(self._camera_id, self._tenant_id)

    def reset(self) -> None:
        self._last_run = 0.0
        self._debouncer.reset()


async def apply_occupancy_tick(
    camera_id: str,
    tenant_id: str,
    transitions: list[SlotTransition],
) -> None:
    event_time = utc_now()
    applied: list[SlotTransition] = []
    alerts: list[Alert] = []
    try:
        session_factory = database.get_session_factory()
        async with session_factory() as db:
            if transitions:
                rows = (
                    await db.execute(
                        select(ParkingSpace).where(
                            ParkingSpace.camera_id == camera_id,
                            ParkingSpace.tenant_id == tenant_id,
                        )
                    )
                ).scalars().all()
                by_id = {row.id: row for row in rows}
                by_code = {row.space_id: row for row in rows}
                for transition in transitions:
                    space = by_id.get(transition.db_id) or by_code.get(
                        transition.space_id
                    )
                    if space is None or bool(space.is_occupied) == transition.occupied:
                        continue
                    checked_out = False
                    if not transition.occupied and space.vehicle_id is not None:
                        checkout = await parking_service.release_space(
                            db,
                            tenant_id,
                            space.id,
                            event_type='vision_checkout',
                        )
                        if checkout is None:
                            continue
                        checked_out = True
                    else:
                        space.is_occupied = transition.occupied
                        # Vision-only occupancy represents an unknown vehicle:
                        # start dwell timing but leave vehicle_id unset until OCR
                        # associates a plate with the bay.
                        space.entry_time = (
                            event_time if transition.occupied else None
                        )
                    space.last_state_change = event_time
                    space.detection_source = 'vision'
                    state_label = 'occupied' if transition.occupied else 'free'
                    if not checked_out:
                        db.add(
                            ParkingActivityLog(
                                tenant_id=tenant_id,
                                timestamp=event_time,
                                event_type=(
                                    'vision_occupied'
                                    if transition.occupied
                                    else 'vision_vacated'
                                ),
                                description=(
                                    f'Space {space.space_id} changed to '
                                    f'{state_label} '
                                    f'(score={transition.score:.3f}).'
                                ),
                                space_id=space.space_id,
                            )
                        )
                    applied.append(transition)

                alerts.extend(
                    parking_anomaly_detector.detect_after_hours_churn(
                        camera_id=camera_id,
                        tenant_id=tenant_id,
                        transitions=applied,
                        now=event_time,
                    )
                )

            alerts.extend(
                await parking_anomaly_detector.detect_ghost_occupancy(
                    db,
                    camera_id=camera_id,
                    tenant_id=tenant_id,
                    now=event_time,
                )
            )
            db.add_all(alerts)
            if applied or alerts:
                await db.commit()

        if applied:
            await ws_manager.broadcast_to_channel(
                tenant_id,
                'parking',
                {
                    'type': 'parking',
                    'data': {
                        'event': 'occupancy',
                        'camera_id': camera_id,
                        'slots': [transition.to_dict() for transition in applied],
                    },
                },
            )
        for alert in alerts:
            await ws_manager.broadcast_alert(
                {
                    'id': alert.id,
                    'camera_id': camera_id,
                    'type': alert.type,
                    'severity': alert.severity,
                    'description': alert.description,
                    'timestamp': alert.timestamp.isoformat() + 'Z',
                    'metadata': alert.metadata_,
                },
                tenant_id=tenant_id,
            )
    except Exception:
        logger.exception('Failed to apply parking occupancy tick for camera %s', camera_id)


async def persist_anomaly_alerts(alerts: list[Alert]) -> None:
    if not alerts:
        return
    try:
        session_factory = database.get_session_factory()
        async with session_factory() as db:
            db.add_all(alerts)
            await db.commit()
        for alert in alerts:
            await ws_manager.broadcast_alert(
                {
                    'id': alert.id,
                    'camera_id': alert.camera_id,
                    'type': alert.type,
                    'severity': alert.severity,
                    'description': alert.description,
                    'timestamp': alert.timestamp.isoformat() + 'Z',
                    'metadata': alert.metadata_,
                },
                tenant_id=alert.tenant_id,
            )
    except Exception:
        logger.exception('Failed to persist parking anomaly alerts')
