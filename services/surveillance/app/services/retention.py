import asyncio
import logging
from datetime import timedelta
from sqlalchemy import delete
from app import database
from app.config import get_settings
from app.models import AnalyticsSnapshot, Alert, AlertStatus, Detection, RoiEvent
from app.utils import utc_now

logger = logging.getLogger(__name__)

async def retention_worker():
    """Background task to delete old detections and alerts to free disk space."""
    settings = get_settings()
    if not settings.RETENTION_ENABLED:
        logger.info("Data retention worker is disabled by configuration.")
        return

    interval_seconds = max(1, int(settings.RETENTION_RUN_INTERVAL_SECONDS))
    logger.info(
        (
            "Data retention worker started. days=%s interval=%ss "
            "resolved_alerts_only=%s delete_roi_events=%s delete_analytics_snapshots=%s"
        ),
        settings.RETENTION_DAYS,
        interval_seconds,
        settings.RETENTION_DELETE_RESOLVED_ALERTS_ONLY,
        settings.RETENTION_DELETE_ROI_EVENTS,
        settings.RETENTION_DELETE_ANALYTICS_SNAPSHOTS,
    )

    while True:
        try:
            cutoff_date = utc_now() - timedelta(days=max(0, int(settings.RETENTION_DAYS)))

            session_factory = database.get_session_factory()
            async with session_factory() as session:
                del_detections = delete(Detection).where(Detection.timestamp < cutoff_date)
                det_result = await session.execute(del_detections)

                del_alerts = delete(Alert).where(Alert.timestamp < cutoff_date)
                if settings.RETENTION_DELETE_RESOLVED_ALERTS_ONLY:
                    del_alerts = del_alerts.where(Alert.status == AlertStatus.RESOLVED.value)
                alert_result = await session.execute(del_alerts)

                roi_result_count = 0
                if settings.RETENTION_DELETE_ROI_EVENTS:
                    del_roi_events = delete(RoiEvent).where(RoiEvent.timestamp < cutoff_date)
                    roi_result = await session.execute(del_roi_events)
                    roi_result_count = int(roi_result.rowcount or 0)

                analytics_result_count = 0
                if settings.RETENTION_DELETE_ANALYTICS_SNAPSHOTS:
                    del_analytics = delete(AnalyticsSnapshot).where(AnalyticsSnapshot.period_end < cutoff_date)
                    analytics_result = await session.execute(del_analytics)
                    analytics_result_count = int(analytics_result.rowcount or 0)

                await session.commit()

                logger.info(
                    (
                        "Data cleanup: deleted detections=%s alerts=%s roi_events=%s "
                        "analytics_snapshots=%s cutoff=%s"
                    ),
                    int(det_result.rowcount or 0),
                    int(alert_result.rowcount or 0),
                    roi_result_count,
                    analytics_result_count,
                    cutoff_date.isoformat(),
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Error in data retention worker: %s", e)

        await asyncio.sleep(interval_seconds)
