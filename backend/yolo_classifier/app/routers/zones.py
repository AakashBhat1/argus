"""CRUD endpoints for ROI zone management."""

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.models import User
from app.services.auth import get_current_active_user

router = APIRouter(prefix="/zones", tags=["zones"])


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


class ZoneCreate(BaseModel):
    name: str = Field(..., max_length=100)
    points: list[ZonePoint] = Field(..., min_length=3)
    threshold_sec: float = Field(default=5.0, ge=0.5)
    color: list[int] = Field(default=[0, 255, 255])
    camera_ids: Optional[list[str]] = None


class ZoneResponse(BaseModel):
    zone_id: int
    name: str
    points: list[list[float]]
    threshold_sec: float
    color: list[int]
    camera_ids: Optional[list[str]] = None


@router.get("/", response_model=list[ZoneResponse])
async def list_zones(
    camera_id: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
):
    """List all ROI zones, optionally filtered by camera_id."""
    zones = _load_zones()
    result = []
    for z in zones:
        # Filter by camera_id if provided
        zone_cameras = z.get("camera_ids")
        if camera_id and zone_cameras and camera_id not in zone_cameras:
            continue

        # Normalize points to [0,1] for the response
        points = z.get("points", [])
        max_val = max((max(p) for p in points), default=0) if points else 0
        if max_val > 1.0:
            ref_w = z.get("reference_width", 960)
            ref_h = z.get("reference_height", 544)
            points = [[p[0] / ref_w, p[1] / ref_h] for p in points]

        result.append(ZoneResponse(
            zone_id=z.get("zone_id", 0),
            name=z.get("name", ""),
            points=points,
            threshold_sec=z.get("threshold_sec", 5.0),
            color=z.get("color", [0, 255, 255]),
            camera_ids=z.get("camera_ids"),
        ))
    return result


@router.post("/", response_model=ZoneResponse, status_code=201)
async def create_zone(
    data: ZoneCreate,
    current_user: User = Depends(get_current_active_user),
):
    """Create a new ROI zone with normalized [0,1] polygon points."""
    zones = _load_zones()

    # Assign next zone_id
    existing_ids = {z.get("zone_id", 0) for z in zones}
    new_id = max(existing_ids, default=0) + 1

    normalized_points = [[p.x, p.y] for p in data.points]

    new_zone = {
        "zone_id": new_id,
        "name": data.name,
        "points": normalized_points,
        "threshold_sec": data.threshold_sec,
        "color": data.color,
    }
    if data.camera_ids:
        new_zone["camera_ids"] = data.camera_ids

    zones.append(new_zone)
    _save_zones(zones)

    return ZoneResponse(
        zone_id=new_id,
        name=data.name,
        points=normalized_points,
        threshold_sec=data.threshold_sec,
        color=data.color,
        camera_ids=data.camera_ids,
    )


@router.delete("/{zone_id}", status_code=204)
async def delete_zone(
    zone_id: int,
    current_user: User = Depends(get_current_active_user),
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
    current_user: User = Depends(get_current_active_user),
):
    """Update an existing ROI zone."""
    zones = _load_zones()
    found = False
    for i, z in enumerate(zones):
        if z.get("zone_id") == zone_id:
            normalized_points = [[p.x, p.y] for p in data.points]
            zones[i] = {
                "zone_id": zone_id,
                "name": data.name,
                "points": normalized_points,
                "threshold_sec": data.threshold_sec,
                "color": data.color,
            }
            if data.camera_ids:
                zones[i]["camera_ids"] = data.camera_ids
            found = True
            break

    if not found:
        raise HTTPException(status_code=404, detail="Zone not found")

    _save_zones(zones)

    return ZoneResponse(
        zone_id=zone_id,
        name=data.name,
        points=[[p.x, p.y] for p in data.points],
        threshold_sec=data.threshold_sec,
        color=data.color,
        camera_ids=data.camera_ids,
    )
