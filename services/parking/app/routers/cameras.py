"""Parking cameras: registry and stream control (tenant-scoped)."""

from __future__ import annotations

import asyncio
import base64

import cv2
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Alert, Camera, CameraStatus, DetectedPlate, ParkingSpace
from app.schemas import CameraCreate, CameraResponse, CameraUpdate
from app.services.auth import Principal, get_current_active_user, require_admin
from app.services.parking_occupancy_service import invalidate_slots
from app.services.stream_manager import stream_manager
from argus_vision.sources import SourceError, open_capture, validate_camera_source

router = APIRouter(prefix="/parking/cameras", tags=["parking-cameras"])


def _validate_source(stream_url: str) -> None:
    try:
        validate_camera_source(stream_url)
    except SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _camera_or_404(db: AsyncSession, camera_id: str, tenant_id: str) -> Camera:
    camera = (
        await db.execute(select(Camera).where(Camera.id == camera_id, Camera.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera


@router.get("", response_model=list[CameraResponse])
async def list_cameras(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: Principal = Depends(get_current_active_user),
):
    query = select(Camera).where(Camera.tenant_id == current_user.tenant_id)
    if active_only:
        query = query.where(Camera.is_active.is_(True))
    return (await db.execute(query.order_by(Camera.created_at.desc()))).scalars().all()


@router.post("", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
async def create_camera(
    data: CameraCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Principal = Depends(require_admin),
):
    _validate_source(data.stream_url)
    payload = data.model_dump()
    camera = Camera(**payload, tenant_id=current_user.tenant_id)
    db.add(camera)
    await db.flush()
    await db.refresh(camera)
    return camera


@router.get("/{camera_id}", response_model=CameraResponse)
async def get_camera(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Principal = Depends(get_current_active_user),
):
    return await _camera_or_404(db, camera_id, current_user.tenant_id)


@router.put("/{camera_id}", response_model=CameraResponse)
async def update_camera(
    camera_id: str,
    data: CameraUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Principal = Depends(require_admin),
):
    camera = await _camera_or_404(db, camera_id, current_user.tenant_id)
    changes = data.model_dump(exclude_unset=True)
    if "stream_url" in changes:
        _validate_source(changes["stream_url"])
    for key, value in changes.items():
        setattr(camera, key, value)
    await db.flush()
    await db.refresh(camera)
    if "role" in changes or "gate_roi" in changes:
        stream_manager.update_camera_gate(camera_id, camera.role, camera.gate_roi)
    return camera


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Principal = Depends(require_admin),
):
    camera = await _camera_or_404(db, camera_id, current_user.tenant_id)
    await stream_manager.stop_stream(camera_id)
    # Bays and history outlive the camera; only the camera link is cleared.
    await db.execute(
        update(ParkingSpace)
        .where(ParkingSpace.camera_id == camera_id, ParkingSpace.tenant_id == current_user.tenant_id)
        .values(camera_id=None, polygon=None)
    )
    await db.execute(
        update(DetectedPlate).where(DetectedPlate.camera_id == camera_id).values(camera_id=None)
    )
    await db.execute(delete(Alert).where(Alert.camera_id == camera_id))
    invalidate_slots(camera_id, current_user.tenant_id)
    await db.delete(camera)


@router.post("/{camera_id}/start")
async def start_stream(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Principal = Depends(require_admin),
):
    camera = await _camera_or_404(db, camera_id, current_user.tenant_id)
    if not await stream_manager.start_stream(camera):
        camera.status = CameraStatus.ERROR.value
        raise HTTPException(status_code=400, detail="Failed to start stream for this camera source")
    camera.status = CameraStatus.ACTIVE.value
    return {"status": "started", "camera_id": camera_id}


@router.post("/{camera_id}/stop")
async def stop_stream(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Principal = Depends(require_admin),
):
    camera = await _camera_or_404(db, camera_id, current_user.tenant_id)
    await stream_manager.stop_stream(camera_id)
    camera.status = CameraStatus.INACTIVE.value
    return {"status": "stopped", "camera_id": camera_id}


@router.get("/{camera_id}/status")
async def stream_status(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Principal = Depends(get_current_active_user),
):
    await _camera_or_404(db, camera_id, current_user.tenant_id)
    stream = stream_manager.get_stream(camera_id)
    if stream is None:
        return {"camera_id": camera_id, "is_running": False, "gate_ocr": None}
    return stream.status()


@router.get("/{camera_id}/snapshot")
async def snapshot(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Principal = Depends(get_current_active_user),
):
    """One JPEG frame (base64) for slot mapping and gate polygon drawing."""
    camera = await _camera_or_404(db, camera_id, current_user.tenant_id)

    def grab():
        capture = open_capture(camera.stream_url)
        try:
            if not capture.isOpened():
                return None, 0, 0
            ok, frame = capture.read()
            if not ok or frame is None:
                return None, 0, 0
            height, width = frame.shape[:2]
            encoded, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            if not encoded:
                return None, width, height
            return base64.b64encode(buffer.tobytes()).decode("ascii"), width, height
        finally:
            capture.release()

    image, width, height = await asyncio.get_running_loop().run_in_executor(None, grab)
    if not image:
        raise HTTPException(status_code=400, detail="Failed to capture frame from camera source")
    return {"camera_id": camera_id, "image": image, "width": width, "height": height}
