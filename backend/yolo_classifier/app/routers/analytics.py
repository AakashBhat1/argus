from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, Union

from app.database import get_db
from app.models import Detection, Camera, Alert, AlertStatus, User
from app.services.auth import get_current_active_user
from app.utils import utc_now

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/dashboard")
async def dashboard_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    now = utc_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total_cameras = await db.execute(select(func.count(Camera.id)))
    active_cameras = await db.execute(
        select(func.count(Camera.id)).where(Camera.is_active == True)
    )
    today_detections = await db.execute(
        select(func.count(Detection.id)).where(Detection.timestamp >= today_start)
    )
    active_alerts = await db.execute(
        select(func.count(Alert.id)).where(Alert.status == AlertStatus.ACTIVE.value)
    )

    return {
        "total_cameras": total_cameras.scalar(),
        "active_cameras": active_cameras.scalar(),
        "detections_today": today_detections.scalar(),
        "active_alerts": active_alerts.scalar(),
        "timestamp": now.isoformat() + "Z",
    }


def _bucket_to_iso(value: Union[datetime, str, None]) -> Optional[str]:
    """Serialize an hour bucket (datetime on Postgres, string on SQLite) as ISO UTC."""
    if value is None:
        return None
    if isinstance(value, str):
        return value.replace(" ", "T") + "Z"
    return value.isoformat() + "Z"


@router.get("/detections/timeline")
async def detection_timeline(
    camera_id: Optional[str] = None,
    hours: int = Query(default=24, le=168),
    interval_minutes: int = Query(default=60, le=1440),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    cutoff = utc_now() - timedelta(hours=hours)

    # date_trunc is Postgres-only; SQLite (local dev default) needs strftime.
    if db.get_bind().dialect.name == "sqlite":
        bucket = func.strftime("%Y-%m-%d %H:00:00", Detection.timestamp).label("bucket")
    else:
        bucket = func.date_trunc("hour", Detection.timestamp).label("bucket")

    query = select(
        bucket,
        func.count(Detection.id).label("count"),
        func.count(func.distinct(Detection.object_id)).label("unique_objects"),
    ).where(Detection.timestamp >= cutoff)

    if camera_id:
        query = query.where(Detection.camera_id == camera_id)

    query = query.group_by(bucket).order_by(bucket)
    result = await db.execute(query)

    return [
        {
            "timestamp": _bucket_to_iso(row[0]),
            "count": row[1],
            "unique_objects": row[2],
        }
        for row in result.all()
    ]


@router.get("/detections/heatmap")
async def detection_heatmap(
    camera_id: str,
    hours: int = Query(default=24, le=168),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    cutoff = utc_now() - timedelta(hours=hours)

    result = await db.execute(
        select(
            Detection.class_label,
            func.avg(Detection.bbox_x).label("avg_x"),
            func.avg(Detection.bbox_y).label("avg_y"),
            func.count(Detection.id).label("count"),
        )
        .where(Detection.camera_id == camera_id, Detection.timestamp >= cutoff)
        .group_by(Detection.class_label)
    )

    return [
        {
            "class_label": row[0],
            "avg_x": round(row[1], 2),
            "avg_y": round(row[2], 2),
            "count": row[3],
        }
        for row in result.all()
    ]


@router.get("/detections/class-distribution")
async def class_distribution(
    camera_id: Optional[str] = None,
    hours: int = Query(default=24, le=168),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    cutoff = utc_now() - timedelta(hours=hours)

    query = select(
        Detection.class_label,
        func.count(Detection.id).label("count"),
        func.avg(Detection.confidence).label("avg_confidence"),
    ).where(Detection.timestamp >= cutoff)

    if camera_id:
        query = query.where(Detection.camera_id == camera_id)

    query = query.group_by(Detection.class_label).order_by(func.count(Detection.id).desc())
    result = await db.execute(query)

    return [
        {
            "class_label": row[0],
            "count": row[1],
            "avg_confidence": round(row[2], 3),
        }
        for row in result.all()
    ]


@router.get("/performance")
async def system_performance(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    now = utc_now()
    last_minute = now - timedelta(minutes=1)
    last_hour = now - timedelta(hours=1)

    detections_minute = await db.execute(
        select(func.count(Detection.id)).where(Detection.timestamp >= last_minute)
    )
    detections_hour = await db.execute(
        select(func.count(Detection.id)).where(Detection.timestamp >= last_hour)
    )
    avg_confidence = await db.execute(
        select(func.avg(Detection.confidence)).where(Detection.timestamp >= last_hour)
    )

    return {
        "detections_per_minute": detections_minute.scalar(),
        "detections_last_hour": detections_hour.scalar(),
        "avg_confidence": round(avg_confidence.scalar() or 0, 3),
        "timestamp": now.isoformat() + "Z",
    }
