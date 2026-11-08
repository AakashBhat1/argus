"""CRUD endpoints for ROI zones. Every zone belongs to one tenant."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Camera, RoiZoneRecord, User
from app.services import audit
from app.services.auth import get_current_active_user, require_admin
from app.services.zone_store import record_to_dict, zone_store
from argus_vision.roi import ZONE_TYPES, ArmedSchedule

router = APIRouter(prefix="/zones", tags=["zones"])

ZoneType = Literal["restricted", "perimeter", "entrance", "driveway", "parking", "public"]


class ZonePoint(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)


class ScheduleWindow(BaseModel):
    start: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    end: str = Field(..., pattern=r"^\d{2}:\d{2}$")
    days: list[int] = Field(default_factory=lambda: list(range(7)))

    @field_validator("start", "end")
    @classmethod
    def _valid_clock(cls, value: str) -> str:
        hours, minutes = value.split(":")
        if not (0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59):
            raise ValueError("time must be HH:MM in 24h format")
        return value

    @field_validator("days")
    @classmethod
    def _valid_days(cls, days):
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("days must be 0 (Monday) .. 6 (Sunday)")
        return sorted(set(days))


class ArmedScheduleSchema(BaseModel):
    mode: Literal["always", "never", "schedule"] = "always"
    windows: list[ScheduleWindow] = Field(default_factory=list, max_length=14)
    tz: str = Field(default="UTC", max_length=64)


class ZoneCreate(BaseModel):
    name: str = Field(..., max_length=100)
    points: list[ZonePoint] = Field(..., min_length=3)
    threshold_sec: float = Field(default=5.0, ge=0.5)
    color: list[int] = Field(default=[0, 255, 255])
    camera_ids: Optional[list[str]] = None
    zone_type: ZoneType = "restricted"
    armed_schedule: ArmedScheduleSchema = Field(default_factory=ArmedScheduleSchema)
    allowed_classes: list[str] = Field(default_factory=list, max_length=20)


class ZoneResponse(BaseModel):
    zone_id: int
    name: str
    points: list[list[float]]
    threshold_sec: float
    color: list[int]
    camera_ids: Optional[list[str]] = None
    zone_type: str = "restricted"
    armed_schedule: dict = Field(default_factory=lambda: {"mode": "always", "windows": [], "tz": "UTC"})
    allowed_classes: list[str] = Field(default_factory=list)
    armed_now: bool = True


def _to_response(z: dict) -> ZoneResponse:
    points = z.get("points", [])
    max_val = max((max(p) for p in points), default=0) if points else 0
    if max_val > 1.0:
        ref_w = z.get("reference_width", 960)
        ref_h = z.get("reference_height", 544)
        points = [[p[0] / ref_w, p[1] / ref_h] for p in points]
    zone_type = str(z.get("zone_type", "restricted")).lower()
    if zone_type not in ZONE_TYPES:
        zone_type = "restricted"
    schedule = ArmedSchedule.from_dict(z.get("armed_schedule"))
    return ZoneResponse(
        zone_id=z.get("zone_id", 0),
        name=z.get("name", ""),
        points=points,
        threshold_sec=z.get("threshold_sec", 5.0),
        color=z.get("color", [0, 255, 255]),
        camera_ids=z.get("camera_ids"),
        zone_type=zone_type,
        armed_schedule=schedule.to_dict(),
        allowed_classes=[str(c).lower() for c in (z.get("allowed_classes") or [])],
        armed_now=schedule.is_armed(),
    )


def _apply(record: RoiZoneRecord, data: ZoneCreate) -> None:
    record.name = data.name
    record.points = [[p.x, p.y] for p in data.points]
    record.threshold_sec = data.threshold_sec
    record.color = data.color
    record.zone_type = data.zone_type
    record.armed_schedule = data.armed_schedule.model_dump()
    record.allowed_classes = sorted({c.strip().lower() for c in data.allowed_classes if c.strip()})
    if data.camera_ids is not None:  # omitted on update: keep the current binding
        record.camera_ids = data.camera_ids or None


async def _tenant_rows(db: AsyncSession, tenant_id: str) -> list[RoiZoneRecord]:
    query = select(RoiZoneRecord).where(RoiZoneRecord.tenant_id == tenant_id).order_by(RoiZoneRecord.id)
    return list((await db.execute(query)).scalars())


async def _zone_or_404(db: AsyncSession, zone_id: int, tenant_id: str) -> RoiZoneRecord:
    query = select(RoiZoneRecord).where(RoiZoneRecord.id == zone_id, RoiZoneRecord.tenant_id == tenant_id)
    record = (await db.execute(query)).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    return record


async def _require_own_cameras(db: AsyncSession, camera_ids: Optional[list[str]], tenant_id: str) -> None:
    if not camera_ids:
        return
    query = select(Camera.id).where(Camera.id.in_(camera_ids), Camera.tenant_id == tenant_id)
    if set(camera_ids) - set((await db.execute(query)).scalars()):
        raise HTTPException(status_code=422, detail="camera_ids contains an unknown camera")


async def _commit_and_publish(db: AsyncSession, tenant_id: str) -> None:
    """Commit (the audit row shares the transaction), then update the pipelines."""
    await db.commit()
    zone_store.replace_tenant(tenant_id, await _tenant_rows(db, tenant_id))


def _zone_summary(record: RoiZoneRecord) -> dict:
    return {
        "name": record.name,
        "zone_type": record.zone_type,
        "camera_ids": record.camera_ids,
        "points": len(record.points or []),
    }


@router.get("/", response_model=list[ZoneResponse])
async def list_zones(
    camera_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """List the caller's tenant's zones, optionally filtered by camera_id."""
    result = []
    for record in await _tenant_rows(db, current_user.tenant_id):
        if camera_id and record.camera_ids and camera_id not in record.camera_ids:
            continue
        result.append(_to_response(record_to_dict(record)))
    return result


@router.get("/types")
async def list_zone_types(current_user: User = Depends(get_current_active_user)):
    """Zone types and what they mean for the risk engine."""
    return [
        {"type": "restricted", "label": "Restricted", "description": "Nobody should be here while armed (server room, back yard at night)."},
        {"type": "perimeter", "label": "Perimeter", "description": "Fence line / property edge. Appearing here first is suspicious."},
        {"type": "entrance", "label": "Entrance", "description": "Door or gate. Tracks that start here are treated as legitimate arrivals."},
        {"type": "driveway", "label": "Driveway", "description": "Vehicles expected; people walking to/from cars are normal."},
        {"type": "parking", "label": "Parking", "description": "Parking lot area; loitering matters more than presence."},
        {"type": "public", "label": "Public", "description": "Footpath / shared area. Presence alone carries no risk."},
    ]


@router.post("/", response_model=ZoneResponse, status_code=201)
async def create_zone(
    data: ZoneCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new ROI zone with normalized [0,1] polygon points."""
    tenant_id = current_user.tenant_id
    await _require_own_cameras(db, data.camera_ids, tenant_id)
    record = RoiZoneRecord(tenant_id=tenant_id)
    _apply(record, data)
    db.add(record)
    await db.flush()
    audit.record(db, request, current_user, "zone.create", "zone", record.id, _zone_summary(record))
    response = _to_response(record_to_dict(record))
    await _commit_and_publish(db, tenant_id)
    return response


@router.delete("/{zone_id}", status_code=204)
async def delete_zone(
    zone_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an ROI zone by ID."""
    tenant_id = current_user.tenant_id
    record = await _zone_or_404(db, zone_id, tenant_id)
    audit.record(db, request, current_user, "zone.delete", "zone", zone_id, _zone_summary(record))
    await db.delete(record)
    await _commit_and_publish(db, tenant_id)


@router.put("/{zone_id}", response_model=ZoneResponse)
async def update_zone(
    zone_id: int,
    data: ZoneCreate,
    request: Request,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing ROI zone (camera binding is preserved unless provided)."""
    tenant_id = current_user.tenant_id
    record = await _zone_or_404(db, zone_id, tenant_id)
    await _require_own_cameras(db, data.camera_ids, tenant_id)
    _apply(record, data)
    audit.record(db, request, current_user, "zone.update", "zone", zone_id, _zone_summary(record))
    response = _to_response(record_to_dict(record))
    await _commit_and_publish(db, tenant_id)
    return response
