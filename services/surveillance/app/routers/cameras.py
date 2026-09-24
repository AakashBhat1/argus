from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.config import get_settings
from app.database import get_db
from app.models import (
    Camera, CameraStatus, User,
    Detection, Alert, RoiEvent, AnalyticsSnapshot, Track, IntentEvent
)
from app.schemas import CameraCreate, CameraUpdate, CameraResponse
from argus_common.net import StreamTargetError, policy_from_settings, validate_stream_target
from app.services.auth import get_current_active_user
from app.services.stream_manager import _resolve_stream_source, stream_manager

router = APIRouter(prefix="/cameras", tags=["cameras"])

_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv", ".wmv", ".m4v"}


def _validate_stream_url(stream_url: str):
    value = stream_url.strip()
    if not value:
        raise HTTPException(
            status_code=422,
            detail="stream_url must not be empty.",
        )
    if value.lower() == "string":
        raise HTTPException(
            status_code=422,
            detail="Invalid stream_url placeholder 'string'. Use webcam index (e.g. '0'), RTSP URL, or file path.",
        )
    # Local capture device index (e.g. "0", "1")
    if value.isdigit():
        if not get_settings().ALLOW_LOCAL_CAPTURE_DEVICES:
            raise HTTPException(
                status_code=422,
                detail="Local capture devices are disabled (ALLOW_LOCAL_CAPTURE_DEVICES=false).",
            )
        return
    # Allow video:// protocol — resolves to a file in the video/ folder
    if value.startswith("video://"):
        filename = value[len("video://"):]
        if not filename or "/" in filename or "\\" in filename:
            raise HTTPException(
                status_code=422,
                detail="Invalid video filename. Use format: video://filename.mp4",
            )
        resolved_source = _resolve_stream_source(value)
        video_path = Path(resolved_source)
        if resolved_source == value or not video_path.is_file():
            raise HTTPException(
                status_code=422,
                detail=f"Video file '{filename}' not found in video folder.",
            )
        if video_path.suffix.lower() not in _VIDEO_EXTENSIONS:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported video format '{video_path.suffix}'. Allowed: {', '.join(sorted(_VIDEO_EXTENSIONS))}",
            )
        return
    settings = get_settings()
    if "://" not in value:
        raise HTTPException(
            status_code=422,
            detail="stream_url must be an rtsp(s):// URL, a video:// file, or a webcam index.",
        )
    try:
        validate_stream_target(value, policy_from_settings(settings))
    except StreamTargetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/", response_model=list[CameraResponse])
async def list_cameras(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    query = select(Camera).where(Camera.tenant_id == current_user.tenant_id)
    if active_only:
        query = query.where(Camera.is_active == True)
    result = await db.execute(query.order_by(Camera.created_at.desc()))
    return result.scalars().all()


@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.tenant_id == current_user.tenant_id)
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


@router.post("/", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
async def create_camera(
    data: CameraCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    _validate_stream_url(data.stream_url)
    camera_data = data.model_dump()
    camera_data["tenant_id"] = current_user.tenant_id
    camera = Camera(**camera_data)
    db.add(camera)
    await db.flush()
    await db.refresh(camera)
    return camera


@router.put("/{camera_id}", response_model=CameraResponse)
async def update_camera(
    camera_id: str,
    data: CameraUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.tenant_id == current_user.tenant_id)
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    update_data = data.model_dump(exclude_unset=True)
    if "stream_url" in update_data:
        _validate_stream_url(update_data["stream_url"])

    for key, value in update_data.items():
        setattr(camera, key, value)

    await db.flush()
    await db.refresh(camera)

    if "calibration" in update_data:
        stream_manager.update_camera_calibration(camera_id, camera.calibration)
    if "role" in update_data or "gate_roi" in update_data:
        stream_manager.update_camera_gate(camera_id, camera.role, camera.gate_roi)
    return camera


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.tenant_id == current_user.tenant_id)
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
        
    # Manually cascade delete dependent records to avoid Postgres FK violation
    # since tracks and intent_events don't have proper ON DELETE CASCADE in the schema yet.
    await db.execute(delete(IntentEvent).where(IntentEvent.camera_id == camera_id))
    await db.execute(delete(Track).where(Track.camera_id == camera_id))
    await db.execute(delete(Detection).where(Detection.camera_id == camera_id))
    await db.execute(delete(Alert).where(Alert.camera_id == camera_id))
    await db.execute(delete(RoiEvent).where(RoiEvent.camera_id == camera_id))
    await db.execute(delete(AnalyticsSnapshot).where(AnalyticsSnapshot.camera_id == camera_id))

    await db.delete(camera)
    await db.commit()
