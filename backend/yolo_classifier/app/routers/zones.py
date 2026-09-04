"""CRUD endpoints for ROI zone management."""

import json
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.detection.roi import ZONE_TYPES, ArmedSchedule
from app.models import User
from app.services.auth import get_current_active_user, require_admin

router = APIRouter(prefix="/zones", tags=["zones"])

ZoneType = Literal["restricted", "perimeter", "entrance", "driveway", "parking", "public"]


def _zones_config_path() -> Path:
    settings = get_settings()
    path = Path(settings.ROI_ZONES_CONFIG_PATH)
    if path.is_absolute():
        return path
    # Resolve relative to backend root (parents[3] from this file)
    # routers/zones.py -> app -> yolo_classifier -> backend
    backend_root = Path(__file__).resolve().parents[3]
    return backend_root / path


def _load_zones() -> list[dict]:
    path = _zones_config_path()
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("zones", [])
    return data


def _save_zones(zones: list[dict]) -> None:
    path = _zones_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(zones, f, indent=2)


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


def _serialize(zone_id: int, data: ZoneCreate, existing: Optional[dict] = None) -> dict:
    payload = {
        "zone_id": zone_id,
        "name": data.name,
        "points": [[p.x, p.y] for p in data.points],
        "threshold_sec": data.threshold_sec,
        "color": data.color,
        "zone_type": data.zone_type,
        "armed_schedule": data.armed_schedule.model_dump(),
        "allowed_classes": sorted({c.strip().lower() for c in data.allowed_classes if c.strip()}),
    }
    camera_ids = data.camera_ids if data.camera_ids is not None else (existing or {}).get("camera_ids")
    if camera_ids:
        payload["camera_ids"] = camera_ids
    return payload


@router.get("/", response_model=list[ZoneResponse])
async def list_zones(
    camera_id: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
):
    """List all ROI zones, optionally filtered by camera_id."""
    zones = _load_zones()
    result = []
    for z in zones:
        zone_cameras = z.get("camera_ids")
        if camera_id and zone_cameras and camera_id not in zone_cameras:
            continue
        result.append(_to_response(z))
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
    current_user: User = Depends(require_admin),
):
    """Create a new ROI zone with normalized [0,1] polygon points."""
    zones = _load_zones()
    existing_ids = {z.get("zone_id", 0) for z in zones}
    new_id = max(existing_ids, default=0) + 1
    new_zone = _serialize(new_id, data)
    zones.append(new_zone)
    _save_zones(zones)
    return _to_response(new_zone)


@router.delete("/{zone_id}", status_code=204)
async def delete_zone(
    zone_id: int,
    current_user: User = Depends(require_admin),
):
    """Delete an ROI zone by ID."""
    zones = _load_zones()
    filtered = [z for z in zones if z.get("zone_id") != zone_id]
    if len(filtered) == len(zones):
        raise HTTPException(status_code=404, detail="Zone not found")
    _save_zones(filtered)


@router.put("/{zone_id}", response_model=ZoneResponse)
async def update_zone(
    zone_id: int,
    data: ZoneCreate,
    current_user: User = Depends(require_admin),
):
    """Update an existing ROI zone (camera binding is preserved unless provided)."""
    zones = _load_zones()
    for i, z in enumerate(zones):
        if z.get("zone_id") == zone_id:
            zones[i] = _serialize(zone_id, data, existing=z)
            _save_zones(zones)
            return _to_response(zones[i])
    raise HTTPException(status_code=404, detail="Zone not found")
