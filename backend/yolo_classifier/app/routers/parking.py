"""Parking REST + WS endpoints — auth + tenant on every route."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.schemas import (
    DetectedPlateResponse,
    ParkingActivityResponse,
    ParkingSpaceResponse,
    ParkingStatsResponse,
    ReleaseSpaceResponse,
)
from app.services import parking_service
from app.services.auth import get_current_active_user, require_admin
from app.services.parking_seeder import seed_parking_spaces_for_tenant
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
    )


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
