from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional

from app.database import get_db
from app.models import Detection, User
from app.schemas import DetectionResponse
from app.services.auth import get_current_active_user
from app.utils import utc_now

router = APIRouter(prefix="/detections", tags=["detections"])


@router.get("/", response_model=list[DetectionResponse])
async def list_detections(
    camera_id: Optional[str] = None,
    class_label: Optional[str] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    query = select(Detection)
    if camera_id:
        query = query.where(Detection.camera_id == camera_id)
    if class_label:
        query = query.where(Detection.class_label == class_label)
    if start_time:
        query = query.where(Detection.timestamp >= start_time)
    if end_time:
        query = query.where(Detection.timestamp <= end_time)

    query = query.order_by(Detection.timestamp.desc()).offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/recent/{camera_id}", response_model=list[DetectionResponse])
async def get_recent_detections(
    camera_id: str,
    seconds: int = 60,
    db: AsyncSession = Depends(get_db),
):
    cutoff = utc_now() - timedelta(seconds=seconds)
    query = (
        select(Detection)
        .where(Detection.camera_id == camera_id, Detection.timestamp >= cutoff)
        .order_by(Detection.timestamp.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/count")
async def count_detections(
    camera_id: Optional[str] = None,
    class_label: Optional[str] = None,
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
):
    cutoff = utc_now() - timedelta(hours=hours)
    query = select(func.count(Detection.id)).where(Detection.timestamp >= cutoff)
    if camera_id:
        query = query.where(Detection.camera_id == camera_id)
    if class_label:
        query = query.where(Detection.class_label == class_label)

    result = await db.execute(query)
    return {"count": result.scalar(), "hours": hours}


@router.get("/classes")
async def get_detection_classes(
    camera_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Detection.class_label, func.count(Detection.id).label("count"))
    if camera_id:
        query = query.where(Detection.camera_id == camera_id)
    query = query.group_by(Detection.class_label).order_by(func.count(Detection.id).desc())
    result = await db.execute(query)
    return [{"class_label": row[0], "count": row[1]} for row in result.all()]
