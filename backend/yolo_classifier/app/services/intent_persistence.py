"""Persistence layer for tracks and intent events.

Uses the same psycopg2 connection pool pattern as events.py for
synchronous DB writes from the inference thread.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import Json
from psycopg2.pool import ThreadedConnectionPool

from app.detection.events import _normalize_psycopg2_dsn, _pool_max_from_env
from app.config import get_settings
from app.services.intent_classifier import IntentResult
from app.services.trajectory import TrajectoryFeatures
from app.utils import utc_now

logger = logging.getLogger(__name__)


class IntentPersistence:
    """Write Track and IntentEvent rows to PostgreSQL."""

    def __init__(self) -> None:
        self._pool: ThreadedConnectionPool | None = None
        self._lock = threading.Lock()
        self._warned = False
        self._last_error_log = 0.0

    def _ensure_pool(self) -> ThreadedConnectionPool | None:
        if self._pool is not None:
            return self._pool

        with self._lock:
            if self._pool is not None:
                return self._pool

            import os
            settings = get_settings()
            raw = os.getenv("DATABASE_URL", "").strip() or str(settings.DATABASE_URL).strip()
            dsn = _normalize_psycopg2_dsn(raw)
            if not dsn:
                if not self._warned:
                    logger.warning("No Postgres DSN — intent events will not be persisted.")
                    self._warned = True
                return None

            try:
                self._pool = ThreadedConnectionPool(
                    minconn=1,
                    maxconn=_pool_max_from_env("INTENT_PG_POOL_MAX", 8),
                    dsn=dsn,
                )
                return self._pool
            except Exception as exc:
                if not self._warned:
                    logger.warning("Failed to init intent persistence pool: %s", exc)
                    self._warned = True
                return None

    def close(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.closeall()
                self._pool = None

    def save_track_and_intent(
        self,
        camera_id: str,
        features: TrajectoryFeatures,
        intent: IntentResult,
        tenant_id: str = "1",
    ) -> bool:
        pool = self._ensure_pool()
        if pool is None:
            return False

        conn = None
        try:
            conn = pool.getconn()
            track_id = str(uuid.uuid4())
            intent_id = str(uuid.uuid4())
            now = utc_now()

            started_at = now - timedelta(seconds=features.duration_sec)
            ended_at = now

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO tracks (
                        id, camera_id, tenant_id, object_id, class_label,
                        started_at, ended_at, duration_sec,
                        total_distance, avg_speed, max_speed,
                        direction_changes, stationary_ratio, bbox_coverage,
                        entry_point, exit_point,
                        roi_zones_visited, had_intrusion,
                        trajectory, feature_vector
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        track_id, camera_id, tenant_id,
                        features.object_id, features.class_label,
                        started_at, ended_at, round(features.duration_sec, 2),
                        round(features.total_distance, 1),
                        round(features.avg_speed, 2),
                        round(features.max_speed, 2),
                        features.direction_changes,
                        round(features.stationary_ratio, 3),
                        round(features.bbox_coverage, 4),
                        Json(features.entry_point),
                        Json(features.exit_point),
                        Json(features.roi_zones_visited),
                        features.had_intrusion,
                        Json(features.trajectory_points),
                        Json(features.to_feature_dict()),
                    ),
                )

                cur.execute(
                    """
                    INSERT INTO intent_events (
                        id, track_id, camera_id, tenant_id,
                        object_id, class_label, intent_type,
                        confidence, reasoning, classifier_version,
                        timestamp, features, metadata
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    (
                        intent_id, track_id, camera_id, tenant_id,
                        features.object_id, features.class_label,
                        intent.intent_type,
                        round(intent.confidence, 3),
                        intent.reasoning,
                        "rule_v1",
                        now,
                        Json(intent.features),
                        Json({
                            "entry_point": features.entry_point,
                            "exit_point": features.exit_point,
                            "trajectory_point_count": features.point_count,
                        }),
                    ),
                )

            conn.commit()
            logger.info(
                "Saved track+intent: object=%d class=%s intent=%s conf=%.2f dur=%.1fs",
                features.object_id, features.class_label,
                intent.intent_type, intent.confidence, features.duration_sec,
            )
            return True

        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            now_mono = time.monotonic()
            if (now_mono - self._last_error_log) >= 30:
                logger.warning("Failed to save track+intent: %s", exc)
                self._last_error_log = now_mono
            return False
        finally:
            if conn is not None:
                pool.putconn(conn)


_INTENT_PERSISTENCE = IntentPersistence()
atexit.register(_INTENT_PERSISTENCE.close)


def save_track_and_intent(
    camera_id: str,
    features: TrajectoryFeatures,
    intent: IntentResult,
    tenant_id: str = "1",
) -> bool:
    return _INTENT_PERSISTENCE.save_track_and_intent(
        camera_id=camera_id,
        features=features,
        intent=intent,
        tenant_id=tenant_id,
    )
