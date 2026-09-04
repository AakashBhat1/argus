"""Security console endpoints: arm state, authorization grants, live risk."""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.detection.geometry import DEFAULT_CLASS_SIZES_M
from app.models import User
from app.services.auth import get_current_active_user, require_admin
from app.services.authorization import ARM_MODES, authorization_registry
from app.services.stream_manager import stream_manager
from app.services.websocket_manager import ws_manager

router = APIRouter(prefix="/security", tags=["security"])


class ArmRequest(BaseModel):
    mode: Literal["armed", "disarmed", "auto"]


class SiteGrantRequest(BaseModel):
    minutes: float = Field(default=15.0, ge=1.0, le=24 * 60)
    label: str = Field(default="Expected visitor", max_length=120)
    note: Optional[str] = Field(default=None, max_length=500)


class TrackGrantRequest(BaseModel):
    camera_id: str = Field(..., max_length=64)
    track_id: int = Field(..., ge=0)
    label: str = Field(default="Known person", max_length=120)
    minutes: float = Field(default=15.0, ge=1.0, le=24 * 60)
    note: Optional[str] = Field(default=None, max_length=500)


class VehicleRegisterRequest(BaseModel):
    """Manual/testing hook mirroring what the gate OCR does automatically."""

    camera_id: str = Field(..., max_length=64)
    track_id: int = Field(..., ge=0)
    plate_text: str = Field(..., min_length=2, max_length=20)
    profile_type: Literal["normal", "vip", "resident", "staff", "blacklist"] = "vip"
    owner_name: Optional[str] = Field(default=None, max_length=120)


async def _broadcast_state(tenant_id: str, event: dict) -> None:
    payload = {"event": event, "state": authorization_registry.snapshot(tenant_id)}
    await ws_manager.broadcast_security(payload, tenant_id=tenant_id)


@router.get("/state")
async def get_state(current_user: User = Depends(get_current_active_user)):
    """Arm mode, active grants and per-camera live risk summary."""
    tenant_id = current_user.tenant_id
    state = authorization_registry.snapshot(tenant_id)
    state["cameras"] = _live_risk(tenant_id)
    state["arm_modes"] = list(ARM_MODES)
    return state


def _live_risk(tenant_id: str) -> list[dict]:
    result = []
    for status in stream_manager.get_all_status():
        stream = stream_manager.get_stream(status["camera_id"])
        if stream is None or stream.tenant_id != tenant_id:
            continue
        summary = stream.pipeline.risk_engine.summary()
        result.append(
            {
                "camera_id": status["camera_id"],
                "camera_name": status["camera_name"],
                "is_running": status["is_running"],
                "risk": summary,
                "ground_plane_calibrated": stream.pipeline.geometry.has_ground_plane,
                "hfov_deg": stream.pipeline.geometry.calibration.hfov_deg,
            }
        )
    return result


@router.put("/arm")
async def set_arm_mode(
    data: ArmRequest,
    current_user: User = Depends(require_admin),
):
    tenant_id = current_user.tenant_id
    mode = authorization_registry.set_arm_mode(tenant_id, data.mode, actor=current_user.username)
    await _broadcast_state(tenant_id, {"type": "arm_mode", "mode": mode, "actor": current_user.username})
    return {"arm_mode": mode}


@router.get("/grants")
async def list_grants(current_user: User = Depends(get_current_active_user)):
    return authorization_registry.list_grants(current_user.tenant_id)


@router.post("/grants/site", status_code=201)
async def grant_site(
    data: SiteGrantRequest,
    current_user: User = Depends(require_admin),
):
    tenant_id = current_user.tenant_id
    grant = authorization_registry.grant_site(
        tenant_id, minutes=data.minutes, label=data.label, note=data.note
    )
    await _broadcast_state(tenant_id, {"type": "grant_added", "grant": grant.to_dict()})
    return grant.to_dict()


@router.post("/grants/track", status_code=201)
async def grant_track(
    data: TrackGrantRequest,
    current_user: User = Depends(require_admin),
):
    tenant_id = current_user.tenant_id
    stream = stream_manager.get_stream(data.camera_id)
    if stream is not None and stream.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Camera not found")
    grant = authorization_registry.grant_track(
        tenant_id,
        camera_id=data.camera_id,
        track_id=data.track_id,
        label=data.label,
        minutes=data.minutes,
        note=data.note,
    )
    await _broadcast_state(tenant_id, {"type": "grant_added", "grant": grant.to_dict()})
    return grant.to_dict()


@router.post("/vehicles/register", status_code=201)
async def register_vehicle(
    data: VehicleRegisterRequest,
    current_user: User = Depends(require_admin),
):
    """Simulate a gate plate read (demo / integration testing)."""
    tenant_id = current_user.tenant_id
    grant = authorization_registry.register_vehicle_plate(
        tenant_id,
        camera_id=data.camera_id,
        track_id=data.track_id,
        plate_text=data.plate_text.upper(),
        profile_type=data.profile_type,
        owner_name=data.owner_name,
    )
    await _broadcast_state(
        tenant_id,
        {"type": "vehicle_registered", "plate_text": data.plate_text.upper(), "grant": grant.to_dict() if grant else None},
    )
    return {"plate_text": data.plate_text.upper(), "grant": grant.to_dict() if grant else None}


@router.delete("/grants/{grant_id}", status_code=204)
async def revoke_grant(
    grant_id: str,
    current_user: User = Depends(require_admin),
):
    tenant_id = current_user.tenant_id
    if not authorization_registry.revoke(tenant_id, grant_id):
        raise HTTPException(status_code=404, detail="Grant not found")
    await _broadcast_state(tenant_id, {"type": "grant_revoked", "grant_id": grant_id})


@router.get("/geometry/defaults")
async def geometry_defaults(current_user: User = Depends(get_current_active_user)):
    """Class size priors used by the pinhole distance estimator."""
    return {"class_sizes_m": DEFAULT_CLASS_SIZES_M}
