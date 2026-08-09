"""Async parking business logic — fully tenant-scoped.

Every function takes/propagates tenant_id. No HTTP/router coupling.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import (
    DetectedPlate,
    ParkingActivityLog,
    ParkingSession,
    ParkingSpace,
    VehicleProfile,
    generate_uuid,
)
from app.services.ocr_service import normalize_plate_text
from app.utils import utc_now

logger = logging.getLogger(__name__)


def calculate_tariff(duration_minutes: int) -> float:
    """Compute parking fee from duration.

    Rules (from v1 parking system):
    - duration < free_minutes → short_stay_rate
    - else ceil(hours) * rate_per_hour
    """
    settings = get_settings()
    if duration_minutes < 0:
        duration_minutes = 0
    free = int(settings.PARKING_FREE_MINUTES)
    short_rate = float(settings.PARKING_SHORT_STAY_RATE)
    hourly = float(settings.PARKING_RATE_PER_HOUR)
    if duration_minutes <= free:
        return short_rate
    hours = math.ceil(duration_minutes / 60.0)
    return float(hours * hourly)


async def log_activity(
    db: AsyncSession,
    tenant_id: str,
    event_type: str,
    description: str,
    *,
    plate_text: Optional[str] = None,
    space_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
) -> ParkingActivityLog:
    row = ParkingActivityLog(
        id=generate_uuid(),
        tenant_id=tenant_id,
        timestamp=utc_now(),
        event_type=event_type,
        description=description,
        plate_text=plate_text,
        space_id=space_id,
        actor_user_id=actor_user_id,
    )
    db.add(row)
    await db.flush()
    return row


async def get_stats(db: AsyncSession, tenant_id: str) -> dict[str, Any]:
    total_q = await db.execute(
        select(func.count(ParkingSpace.id)).where(ParkingSpace.tenant_id == tenant_id)
    )
    total = int(total_q.scalar_one() or 0)
    occ_q = await db.execute(
        select(func.count(ParkingSpace.id)).where(
            ParkingSpace.tenant_id == tenant_id,
            ParkingSpace.is_occupied.is_(True),
        )
    )
    occupied = int(occ_q.scalar_one() or 0)
    free = total - occupied

    today_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
    plates_q = await db.execute(
        select(func.count(DetectedPlate.id)).where(
            DetectedPlate.tenant_id == tenant_id,
            DetectedPlate.timestamp >= today_start,
        )
    )
    plates_today = int(plates_q.scalar_one() or 0)

    return {
        "total": total,
        "occupied": occupied,
        "free": free,
        "available": free,
        "occupancy_pct": round(occupied / total * 100, 1) if total > 0 else 0.0,
        "plates_today": plates_today,
    }


async def list_spaces(
    db: AsyncSession,
    tenant_id: str,
) -> list[ParkingSpace]:
    result = await db.execute(
        select(ParkingSpace)
        .options(selectinload(ParkingSpace.vehicle))
        .where(ParkingSpace.tenant_id == tenant_id)
        .order_by(ParkingSpace.zone, ParkingSpace.space_id)
    )
    return list(result.scalars().all())


async def get_space_by_id(
    db: AsyncSession,
    tenant_id: str,
    space_pk_or_code: str,
) -> Optional[ParkingSpace]:
    """Lookup by surrogate id OR human space_id within tenant."""
    result = await db.execute(
        select(ParkingSpace)
        .options(selectinload(ParkingSpace.vehicle))
        .where(
            ParkingSpace.tenant_id == tenant_id,
            (ParkingSpace.id == space_pk_or_code)
            | (ParkingSpace.space_id == space_pk_or_code),
        )
    )
    return result.scalar_one_or_none()


async def get_or_create_profile(
    db: AsyncSession,
    tenant_id: str,
    plate_text: str,
    *,
    profile_type: str = "normal",
    owner_name: str = "Visitor",
    notes: Optional[str] = None,
) -> VehicleProfile:
    plate = normalize_plate_text(plate_text)
    result = await db.execute(
        select(VehicleProfile).where(
            VehicleProfile.tenant_id == tenant_id,
            VehicleProfile.plate_text == plate,
        )
    )
    profile = result.scalar_one_or_none()
    if profile:
        return profile
    profile = VehicleProfile(
        id=generate_uuid(),
        tenant_id=tenant_id,
        plate_text=plate,
        profile_type=profile_type,
        owner_name=owner_name,
        notes=notes,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(profile)
    await db.flush()
    return profile


async def record_detected_plate(
    db: AsyncSession,
    tenant_id: str,
    plate_text: str,
    *,
    state: Optional[str] = None,
    confidence: float = 0.0,
    camera_id: Optional[str] = None,
    track_id: Optional[str] = None,
    vehicle_id: Optional[str] = None,
) -> DetectedPlate:
    plate = normalize_plate_text(plate_text)
    if vehicle_id is None:
        profile = await get_or_create_profile(db, tenant_id, plate)
        vehicle_id = profile.id

    row = DetectedPlate(
        id=generate_uuid(),
        tenant_id=tenant_id,
        plate_text=plate,
        vehicle_id=vehicle_id,
        camera_id=camera_id,
        track_id=str(track_id) if track_id is not None else None,
        state=state,
        timestamp=utc_now(),
        is_parked=False,
        confidence=float(confidence or 0.0),
        amount_paid=0.0,
    )
    db.add(row)

    # Bump current session counter
    session = await _current_session(db, tenant_id)
    if session:
        session.plates_detected = int(session.plates_detected or 0) + 1

    await log_activity(
        db,
        tenant_id,
        "plate_detected",
        f"Detected plate: {plate} ({state or 'Unknown'})",
        plate_text=plate,
    )
    await db.flush()
    return row


async def assign_space(
    db: AsyncSession,
    tenant_id: str,
    plate_text: str,
) -> Optional[dict[str, Any]]:
    """Assign first free space (VIP → ground floor preferred)."""
    plate = normalize_plate_text(plate_text)
    profile = await get_or_create_profile(db, tenant_id, plate)
    profile_type = profile.profile_type or "normal"

    if profile_type == "blacklist":
        await log_activity(
            db,
            tenant_id,
            "security_alert",
            f"BOLO ALERT: Blacklisted vehicle {plate} detected! Notify security.",
            plate_text=plate,
        )

    space: Optional[ParkingSpace] = None
    if profile_type == "vip":
        res = await db.execute(
            select(ParkingSpace)
            .where(
                ParkingSpace.tenant_id == tenant_id,
                ParkingSpace.is_occupied.is_(False),
                ParkingSpace.floor == "G",
            )
            .order_by(ParkingSpace.space_id.asc())
            .limit(1)
        )
        space = res.scalar_one_or_none()

    if space is None:
        floor_order = case(
            (ParkingSpace.floor == "G", 1),
            (ParkingSpace.floor == "1", 2),
            (ParkingSpace.floor == "2", 3),
            else_=9,
        )
        res = await db.execute(
            select(ParkingSpace)
            .where(
                ParkingSpace.tenant_id == tenant_id,
                ParkingSpace.is_occupied.is_(False),
            )
            .order_by(floor_order.asc(), ParkingSpace.space_id.asc())
            .limit(1)
        )
        space = res.scalar_one_or_none()

    if space is None:
        return None

    now = utc_now()
    space.is_occupied = True
    space.vehicle_id = profile.id
    space.entry_time = now

    # Mark latest open detection as parked
    det_res = await db.execute(
        select(DetectedPlate)
        .where(
            DetectedPlate.tenant_id == tenant_id,
            DetectedPlate.plate_text == plate,
            DetectedPlate.is_parked.is_(False),
        )
        .order_by(DetectedPlate.timestamp.desc())
        .limit(1)
    )
    det = det_res.scalar_one_or_none()
    if det:
        det.is_parked = True
        det.vehicle_id = profile.id

    session = await _current_session(db, tenant_id)
    if session:
        session.spaces_used = int(session.spaces_used or 0) + 1

    event = "vip_entry" if profile_type == "vip" else "space_assigned"
    desc = (
        f"VIP {plate} routed to premium spot {space.space_id}"
        if profile_type == "vip"
        else f"Plate {plate} assigned to {space.space_id}"
    )
    await log_activity(
        db, tenant_id, event, desc, plate_text=plate, space_id=space.space_id
    )
    await db.flush()

    return {
        "space_id": space.space_id,
        "space_pk": space.id,
        "plate_text": plate,
        "profile_type": profile_type,
        "owner_name": profile.owner_name,
        "entry_time": now,
    }


async def release_space(
    db: AsyncSession,
    tenant_id: str,
    space_pk_or_code: str,
    *,
    actor_user_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Release occupied space; compute duration + tariff."""
    space = await get_space_by_id(db, tenant_id, space_pk_or_code)
    if space is None or not space.is_occupied:
        return None

    plate_text: Optional[str] = None
    if space.vehicle_id:
        prof = (
            await db.execute(
                select(VehicleProfile).where(
                    VehicleProfile.id == space.vehicle_id,
                    VehicleProfile.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if prof:
            plate_text = prof.plate_text

    now = utc_now()
    duration_min = 0
    if space.entry_time:
        entry = space.entry_time
        if entry.tzinfo is not None:
            entry = entry.replace(tzinfo=None)
        duration_min = max(0, int((now - entry).total_seconds() / 60))

    amount = calculate_tariff(duration_min)
    space_code = space.space_id

    space.is_occupied = False
    space.vehicle_id = None
    space.entry_time = None

    if plate_text:
        det_res = await db.execute(
            select(DetectedPlate)
            .where(
                DetectedPlate.tenant_id == tenant_id,
                DetectedPlate.plate_text == plate_text,
                DetectedPlate.is_parked.is_(True),
            )
            .order_by(DetectedPlate.timestamp.desc())
            .limit(1)
        )
        det = det_res.scalar_one_or_none()
        if det:
            det.is_parked = False
            det.exit_time = now
            det.duration_minutes = duration_min
            det.amount_paid = amount

    await log_activity(
        db,
        tenant_id,
        "space_released",
        f"Space {space_code} released (was {plate_text or 'unknown'}, "
        f"{duration_min} min, paid: {amount} INR)",
        plate_text=plate_text,
        space_id=space_code,
        actor_user_id=actor_user_id,
    )
    await db.flush()

    return {
        "space_id": space_code,
        "plate_text": plate_text,
        "duration_minutes": duration_min,
        "amount_paid": amount,
    }


async def list_plates(
    db: AsyncSession,
    tenant_id: str,
    *,
    limit: int = 50,
) -> list[DetectedPlate]:
    result = await db.execute(
        select(DetectedPlate)
        .where(DetectedPlate.tenant_id == tenant_id)
        .order_by(DetectedPlate.timestamp.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def latest_plate(
    db: AsyncSession,
    tenant_id: str,
) -> Optional[DetectedPlate]:
    result = await db.execute(
        select(DetectedPlate)
        .where(DetectedPlate.tenant_id == tenant_id)
        .order_by(DetectedPlate.timestamp.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_activity(
    db: AsyncSession,
    tenant_id: str,
    *,
    limit: int = 50,
) -> list[ParkingActivityLog]:
    result = await db.execute(
        select(ParkingActivityLog)
        .where(ParkingActivityLog.tenant_id == tenant_id)
        .order_by(ParkingActivityLog.timestamp.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def _current_session(
    db: AsyncSession,
    tenant_id: str,
) -> Optional[ParkingSession]:
    result = await db.execute(
        select(ParkingSession)
        .where(
            ParkingSession.tenant_id == tenant_id,
            ParkingSession.end_time.is_(None),
        )
        .order_by(ParkingSession.start_time.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
