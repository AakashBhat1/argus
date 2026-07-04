import asyncio
import base64
import logging
import platform
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Camera, CameraStatus, User
from app.services.stream_manager import stream_manager, _resolve_stream_source, _open_capture
from app.services.auth import get_current_active_user

router = APIRouter(prefix="/streams", tags=["streams"])
logger = logging.getLogger(__name__)


@router.post("/{camera_id}/start")
async def start_stream(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        result = await db.execute(
            select(Camera).where(
                Camera.id == camera_id, Camera.tenant_id == current_user.tenant_id
            )
        )
        camera = result.scalar_one_or_none()
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")

        success = await stream_manager.start_stream(camera)
        if not success:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Failed to start stream. "
                    "Check camera stream_url and source availability."
                ),
            )

        camera.status = CameraStatus.ACTIVE.value
        await db.flush()
        return {"status": "started", "camera_id": str(camera_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to start stream for camera %s", camera_id)
        raise HTTPException(status_code=500, detail=f"Start stream failed: {e}")


@router.post("/{camera_id}/stop")
async def stop_stream(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        result = await db.execute(
            select(Camera).where(
                Camera.id == camera_id, Camera.tenant_id == current_user.tenant_id
            )
        )
        camera = result.scalar_one_or_none()
        if not camera:
            raise HTTPException(status_code=404, detail="Camera not found")

        await stream_manager.stop_stream(camera_id)
        camera.status = CameraStatus.INACTIVE.value
        await db.flush()
        return {"status": "stopped", "camera_id": str(camera_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to stop stream for camera %s", camera_id)
        raise HTTPException(status_code=500, detail=f"Stop stream failed: {e}")


@router.post("/{camera_id}/pause")
async def pause_stream(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Camera).where(
            Camera.id == camera_id, Camera.tenant_id == current_user.tenant_id
        )
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    if not camera.stream_url.strip().startswith("video://"):
        raise HTTPException(status_code=400, detail="Only video file streams can be paused")
    success = stream_manager.pause_stream(camera_id)
    if not success:
        raise HTTPException(status_code=400, detail="Stream is not running")
    return {"status": "paused", "camera_id": str(camera_id)}


@router.post("/{camera_id}/resume")
async def resume_stream(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Camera).where(
            Camera.id == camera_id, Camera.tenant_id == current_user.tenant_id
        )
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")
    success = stream_manager.resume_stream(camera_id)
    if not success:
        raise HTTPException(status_code=400, detail="Stream is not running")
    return {"status": "resumed", "camera_id": str(camera_id)}


@router.get("/status")
async def all_stream_status(current_user: User = Depends(get_current_active_user)):
    statuses = stream_manager.get_all_status()
    return {"streams": statuses}


@router.get("/{camera_id}/status")
async def stream_status(
    camera_id: str,
    current_user: User = Depends(get_current_active_user),
):
    status = stream_manager.get_stream_status(camera_id)
    if not status:
        raise HTTPException(status_code=404, detail="Stream not found")
    return status


@router.post("/stop-all")
async def stop_all_streams(current_user: User = Depends(get_current_active_user)):
    await stream_manager.stop_all()
    return {"status": "all streams stopped"}


@router.get("/{camera_id}/snapshot")
async def get_snapshot(
    camera_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Grab a single frame from a camera source as base64 JPEG for the zone editor."""
    result = await db.execute(
        select(Camera).where(
            Camera.id == camera_id, Camera.tenant_id == current_user.tenant_id
        )
    )
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="Camera not found")

    loop = asyncio.get_event_loop()

    def _grab_frame():
        cap = _open_capture(camera.stream_url)
        if not cap.isOpened():
            return None, 0, 0
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            return None, 0, 0
        h, w = frame.shape[:2]
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return None, w, h
        return base64.b64encode(buf.tobytes()).decode("ascii"), w, h

    b64, width, height = await loop.run_in_executor(None, _grab_frame)

    if not b64:
        raise HTTPException(status_code=400, detail="Failed to capture frame from camera source")

    return {
        "camera_id": camera_id,
        "image": b64,
        "width": width,
        "height": height,
    }
