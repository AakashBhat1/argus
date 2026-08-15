"""Parking REST + WS endpoints — auth + tenant on every route."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.models import Camera, ParkingSpace, User, generate_uuid
from app.schemas import (
    DetectedPlateResponse,
    ParkingActivityResponse,
    ParkingSlotPreviewRequest,
    ParkingSlotPreviewResponse,
    ParkingSlotsReplaceRequest,
    ParkingSpaceResponse,
    ParkingStatsResponse,
    ReleaseSpaceResponse,
)
from app.services import parking_service
from app.services.auth import get_current_active_user, require_admin
from app.services.parking_seeder import seed_parking_spaces_for_tenant
from app.services.parking_occupancy import OccupancyDetector, SlotGeometry
from app.services.parking_occupancy_service import set_cached_slots, slots_from_rows
from app.services.stream_manager import _open_capture
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/parking", tags=["parking"])


def _space_to_response(space) -> ParkingSpaceResponse:
    plate = None
    profile_type = None
    if space.vehicle is not None:
        plate = space.vehicle.plate_text
        profile_type = space.vehicle.profile_type
    return ParkingSpaceResponse(
        id=space.id,
        tenant_id=space.tenant_id,
        space_id=space.space_id,
        zone=space.zone or "A",
        floor=space.floor or "G",
        is_occupied=bool(space.is_occupied),
        vehicle_id=space.vehicle_id,
        entry_time=space.entry_time,
        plate_text=plate,
        profile_type=profile_type,
        camera_id=space.camera_id,
        polygon=space.polygon,
        display_order=space.display_order or 0,
        detection_source=space.detection_source or 'manual',
        last_state_change=space.last_state_change,
    )


async def _tenant_camera(
    db: AsyncSession, camera_id: str, tenant_id: str
) -> Camera:
    camera = (
        await db.execute(
            select(Camera).where(
                Camera.id == camera_id,
                Camera.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if camera is None:
        raise HTTPException(status_code=404, detail='Camera not found')
    return camera


@router.get("/stats", response_model=ParkingStatsResponse)
async def parking_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await parking_service.get_stats(db, current_user.tenant_id)


@router.get("/spaces", response_model=list[ParkingSpaceResponse])
async def list_parking_spaces(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    seed_if_empty: bool = Query(default=True),
):
    tenant_id = current_user.tenant_id
    spaces = await parking_service.list_spaces(db, tenant_id)
    if not spaces and seed_if_empty:
        await seed_parking_spaces_for_tenant(db, tenant_id)
        spaces = await parking_service.list_spaces(db, tenant_id)
    return [_space_to_response(s) for s in spaces]


@router.post("/spaces/{space_id}/release", response_model=ReleaseSpaceResponse)
async def release_parking_space(
    space_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await parking_service.release_space(
        db,
        current_user.tenant_id,
        space_id,
        actor_user_id=current_user.id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Space not found")
    await ws_manager.broadcast_to_channel(
        current_user.tenant_id,
        "parking",
        {"type": "parking", "data": {"event": "exit", **result}},
    )
    return ReleaseSpaceResponse(**result)


@router.get("/plates", response_model=list[DetectedPlateResponse])
async def list_plates(
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await parking_service.list_plates(db, current_user.tenant_id, limit=limit)


@router.get("/plates/latest", response_model=Optional[DetectedPlateResponse])
async def latest_plate(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await parking_service.latest_plate(db, current_user.tenant_id)


@router.get("/activity", response_model=list[ParkingActivityResponse])
async def list_activity(
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return await parking_service.list_activity(db, current_user.tenant_id, limit=limit)


@router.get(
    '/cameras/{camera_id}/slots',
    response_model=list[ParkingSpaceResponse],
)
async def list_camera_slots(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _tenant_camera(db, camera_id, current_user.tenant_id)
    rows = (
        await db.execute(
            select(ParkingSpace)
            .options(selectinload(ParkingSpace.vehicle))
            .where(
                ParkingSpace.camera_id == camera_id,
                ParkingSpace.tenant_id == current_user.tenant_id,
            )
            .order_by(ParkingSpace.display_order, ParkingSpace.space_id)
        )
    ).scalars().all()
    return [_space_to_response(row) for row in rows]


@router.put(
    '/cameras/{camera_id}/slots',
    response_model=list[ParkingSpaceResponse],
)
async def replace_camera_slots(
    camera_id: str,
    payload: ParkingSlotsReplaceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    tenant_id = current_user.tenant_id
    await _tenant_camera(db, camera_id, tenant_id)
    existing = (
        await db.execute(
            select(ParkingSpace).where(
                ParkingSpace.camera_id == camera_id,
                ParkingSpace.tenant_id == tenant_id,
            )
        )
    ).scalars().all()
    protected = sorted(
        row.space_id
        for row in existing
        if bool(row.is_occupied) or row.vehicle_id is not None
    )
    if protected:
        raise HTTPException(
            status_code=409,
            detail=(
                'Cannot replace slots while bays have active occupancy: '
                + ', '.join(protected)
            ),
        )

    incoming_ids = [slot.space_id for slot in payload.slots]
    if incoming_ids:
        matching_rows = (
            await db.execute(
                select(ParkingSpace).where(
                    ParkingSpace.tenant_id == tenant_id,
                    ParkingSpace.space_id.in_(incoming_ids),
                )
            )
        ).scalars().all()
        collisions = sorted(
            row.space_id
            for row in matching_rows
            if row.camera_id != camera_id
        )
        if collisions:
            raise HTTPException(
                status_code=409,
                detail=(
                    'Space IDs already belong to other or unmapped bays: '
                    + ', '.join(collisions)
                ),
            )

    existing_by_code = {row.space_id: row for row in existing}
    incoming_codes = set(incoming_ids)
    for slot in payload.slots:
        row = existing_by_code.get(slot.space_id)
        if row is None:
            row = ParkingSpace(
                id=generate_uuid(),
                tenant_id=tenant_id,
                camera_id=camera_id,
                space_id=slot.space_id,
                zone='A',
                floor='G',
                is_occupied=False,
            )
            db.add(row)
        row.polygon = [list(point) for point in slot.polygon]
        row.display_order = slot.display_order
        row.detection_source = 'manual'

    for row in existing:
        if row.space_id not in incoming_codes:
            await db.delete(row)

    await db.flush()
    rows = (
        await db.execute(
            select(ParkingSpace)
            .options(selectinload(ParkingSpace.vehicle))
            .where(
                ParkingSpace.camera_id == camera_id,
                ParkingSpace.tenant_id == tenant_id,
            )
            .order_by(ParkingSpace.display_order, ParkingSpace.space_id)
        )
    ).scalars().all()
    set_cached_slots(camera_id, tenant_id, slots_from_rows(rows))
    return [_space_to_response(row) for row in rows]


@router.post('/slots/preview', response_model=ParkingSlotPreviewResponse)
async def preview_camera_slots(
    payload: ParkingSlotPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    camera = await _tenant_camera(db, payload.camera_id, current_user.tenant_id)
    loop = asyncio.get_running_loop()

    def _grab_frame():
        capture = _open_capture(camera.stream_url)
        try:
            if not capture.isOpened():
                return None
            ok, frame = capture.read()
            return frame if ok else None
        finally:
            capture.release()

    frame = await loop.run_in_executor(None, _grab_frame)
    if frame is None:
        raise HTTPException(
            status_code=400,
            detail='Failed to capture frame from camera source',
        )
    settings = get_settings()
    detector = OccupancyDetector(
        hi=settings.PARKING_OCCUPANCY_HI,
        lo=settings.PARKING_OCCUPANCY_LO,
        iou_min=settings.PARKING_OCCUPANCY_IOU_MIN,
    )
    geometries = [
        SlotGeometry(
            space_id=slot.space_id,
            db_id=slot.space_id,
            polygon=slot.polygon,
        )
        for slot in payload.slots
    ]
    readings = detector.score_slots(frame, geometries, [])
    height, width = frame.shape[:2]
    return ParkingSlotPreviewResponse(
        camera_id=payload.camera_id,
        width=width,
        height=height,
        slots=[reading.to_dict() for reading in readings],
    )
