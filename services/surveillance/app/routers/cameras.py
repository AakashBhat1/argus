
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.models import (
    Camera, User,
    Detection, Alert, RoiEvent, AnalyticsSnapshot, Track, IntentEvent
)
from app.schemas import CameraCreate, CameraUpdate, CameraResponse
from argus_vision.sources import SourceError, validate_camera_source
from app.services.auth import get_current_active_user, require_admin
from argus_common.net import MaskedCredentialsError, restore_masked_credentials
from app.services.stream_manager import stream_manager

router = APIRouter(prefix="/cameras", tags=["cameras"])

def _validate_stream_url(stream_url: str) -> None:
    try:
        validate_camera_source(stream_url)
    except SourceError as exc:
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
    current_user: User = Depends(require_admin),
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
    current_user: User = Depends(require_admin),
):
    result = await db.execute(
        select(Camera).where(Camera.id == camera_id, Camera.tenant_id == current_user.tenant_id)
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    update_data = data.model_dump(exclude_unset=True)
    if "stream_url" in update_data:
        # Clients only see masked URLs; one sent back keeps the stored
        # credentials (same address only).
        try:
            update_data["stream_url"] = restore_masked_credentials(update_data["stream_url"], camera.stream_url)
        except MaskedCredentialsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _validate_stream_url(update_data["stream_url"])

    for key, value in update_data.items():
        setattr(camera, key, value)

    await db.flush()
    await db.refresh(camera)

    if "calibration" in update_data:
        stream_manager.update_camera_calibration(camera_id, camera.calibration)
    return camera


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
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
