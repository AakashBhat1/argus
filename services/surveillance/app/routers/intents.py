"""API endpoints for intent classification data."""

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Track, IntentEvent, User
from app.services.auth import get_current_active_user
from app.utils import utc_now

router = APIRouter(prefix="/intents", tags=["intents"])


@router.get("/events")
async def list_intent_events(
    camera_id: Optional[str] = None,
    intent_type: Optional[str] = None,
    hours: int = Query(default=24, le=168),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    cutoff = utc_now() - timedelta(hours=hours)
    query = (
        select(IntentEvent)
        .where(
            IntentEvent.timestamp >= cutoff,
            IntentEvent.tenant_id == current_user.tenant_id,
        )
        .order_by(desc(IntentEvent.timestamp))
        .limit(limit)
    )
    if camera_id:
        query = query.where(IntentEvent.camera_id == camera_id)
    if intent_type:
        query = query.where(IntentEvent.intent_type == intent_type)

    result = await db.execute(query)
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "track_id": row.track_id,
            "camera_id": row.camera_id,
            "object_id": row.object_id,
            "class_label": row.class_label,
            "intent_type": row.intent_type,
            "confidence": row.confidence,
            "reasoning": row.reasoning,
            "classifier_version": row.classifier_version,
            "timestamp": (row.timestamp.isoformat() + "Z") if row.timestamp else None,
            "features": row.features,
        }
        for row in rows
    ]


@router.get("/distribution")
async def intent_distribution(
    camera_id: Optional[str] = None,
    hours: int = Query(default=24, le=168),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    cutoff = utc_now() - timedelta(hours=hours)
    query = (
        select(
            IntentEvent.intent_type,
            func.count(IntentEvent.id).label("count"),
            func.avg(IntentEvent.confidence).label("avg_confidence"),
        )
        .where(
            IntentEvent.timestamp >= cutoff,
            IntentEvent.tenant_id == current_user.tenant_id,
        )
        .group_by(IntentEvent.intent_type)
        .order_by(desc(func.count(IntentEvent.id)))
    )
    if camera_id:
        query = query.where(IntentEvent.camera_id == camera_id)

    result = await db.execute(query)
    return [
        {
            "intent_type": row[0],
            "count": row[1],
            "avg_confidence": round(row[2], 3) if row[2] else 0,
        }
        for row in result.all()
    ]


@router.get("/tracks/{track_id}")
async def get_track(
    track_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = await db.execute(
        select(Track).where(
            Track.id == track_id, Track.tenant_id == current_user.tenant_id
        )
    )
    track = result.scalar_one_or_none()
    if not track:
        return {"error": "Track not found"}

    intent_result = await db.execute(
        select(IntentEvent)
        .where(IntentEvent.track_id == track_id)
        .order_by(desc(IntentEvent.timestamp))
        .limit(1)
    )
    intent = intent_result.scalars().first()

    return {
        "track": {
            "id": track.id,
            "camera_id": track.camera_id,
            "object_id": track.object_id,
            "class_label": track.class_label,
            "started_at": (track.started_at.isoformat() + "Z") if track.started_at else None,
            "ended_at": (track.ended_at.isoformat() + "Z") if track.ended_at else None,
            "duration_sec": track.duration_sec,
            "total_distance": track.total_distance,
            "avg_speed": track.avg_speed,
            "max_speed": track.max_speed,
            "direction_changes": track.direction_changes,
            "stationary_ratio": track.stationary_ratio,
            "bbox_coverage": track.bbox_coverage,
            "entry_point": track.entry_point,
            "exit_point": track.exit_point,
            "roi_zones_visited": track.roi_zones_visited,
            "had_intrusion": track.had_intrusion,
            "trajectory": track.trajectory,
            "feature_vector": track.feature_vector,
        },
        "intent": {
            "intent_type": intent.intent_type,
            "confidence": intent.confidence,
            "reasoning": intent.reasoning,
            "classifier_version": intent.classifier_version,
        } if intent else None,
    }
