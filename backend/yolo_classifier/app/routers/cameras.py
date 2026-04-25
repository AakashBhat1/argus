import ipaddress
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db
from app.models import (
    Camera, CameraStatus, User,
    Detection, Alert, RoiEvent, AnalyticsSnapshot, Track, IntentEvent
)
from app.schemas import CameraCreate, CameraUpdate, CameraResponse
from app.services.auth import get_current_active_user

router = APIRouter(prefix="/cameras", tags=["cameras"])

_ALLOWED_SCHEMES = {"rtsp", "rtsps", "http", "https"}

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
]

_VIDEO_DIR = Path(__file__).resolve().parents[2] / "video"
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
    # Allow webcam index (e.g. "0", "1")
    if value.isdigit():
        return
    # Allow video:// protocol — resolves to a file in the video/ folder
    if value.startswith("video://"):
        filename = value[len("video://"):]
        if not filename or "/" in filename or "\\" in filename:
            raise HTTPException(
                status_code=422,
                detail="Invalid video filename. Use format: video://filename.mp4",
            )
        video_path = _VIDEO_DIR / filename
        if not video_path.is_file():
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
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme and scheme not in _ALLOWED_SCHEMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported URL scheme '{scheme}'. Only rtsp://, rtsps://, or video:// are allowed.",
        )
    # Note: private/local IPs are allowed for local camera streams (DroidCam, IP webcam, etc.)


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
