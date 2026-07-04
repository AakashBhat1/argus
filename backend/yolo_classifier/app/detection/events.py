"""Detection event logging utilities."""

from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import Json
from psycopg2.pool import ThreadedConnectionPool

from app.config import get_settings
from app.detection.roi import IntrusionEvent
from app.utils import utc_now

logger = logging.getLogger(__name__)

_DEFAULT_ALLOWED_CLASSES = {
    "person",
    "car",
    "bus",
    "truck",
    "motorcycle",
    "bicycle",
}

_COCO_ALLOWED_CLASS_ID = {
    "person": 0,
    "bicycle": 1,
    "car": 2,
    "motorcycle": 3,
    "bus": 5,
    "truck": 7,
}


def _pool_max_from_env(var_name: str, default: int) -> int:
    """Read a connection-pool size from the environment, falling back safely."""
    try:
        value = int(os.getenv(var_name, str(default)))
    except ValueError:
        return default
    return value if value >= 1 else default


def _normalize_psycopg2_dsn(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""
    if value.startswith("postgresql+asyncpg://"):
        return value.replace("postgresql+asyncpg://", "postgresql://", 1)
    if value.startswith("postgresql+psycopg2://"):
        return value.replace("postgresql+psycopg2://", "postgresql://", 1)
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql://", 1)
    if value.startswith("postgresql://"):
        return value
    return ""


class _RoiEventPostgresWriter:
    def __init__(self) -> None:
        self._pool: ThreadedConnectionPool | None = None
        self._lock = threading.Lock()
        self._warned_missing_url = False
        self._warned_connect_error = False
        self._last_insert_error_log = 0.0

    def _resolve_dsn(self) -> str:
        settings = get_settings()
        raw = os.getenv("DATABASE_URL", "").strip() or str(settings.DATABASE_URL).strip()
        return _normalize_psycopg2_dsn(raw)

    def _ensure_pool(self) -> ThreadedConnectionPool | None:
        pool = self._pool
        if pool is not None:
            return pool

        with self._lock:
            if self._pool is not None:
                return self._pool

            dsn = self._resolve_dsn()
            if not dsn:
                if not self._warned_missing_url:
                    logger.warning(
                        "DATABASE_URL is missing or not a Postgres URL. "
                        "ROI events will not be inserted into PostgreSQL."
                    )
                    self._warned_missing_url = True
                return None

            try:
                self._pool = ThreadedConnectionPool(
                    minconn=1,
                    maxconn=_pool_max_from_env("ROI_PG_POOL_MAX", 16),
                    dsn=dsn,
                )
                return self._pool
            except Exception as exc:
                if not self._warned_connect_error:
                    logger.warning("Failed to initialize ROI Postgres connection pool: %s", exc)
                    self._warned_connect_error = True
                return None

    def close(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.closeall()
                self._pool = None

    def insert_roi_event(self, event: dict) -> bool:
        pool = self._ensure_pool()
        if pool is None:
            return False

        conn = None
        try:
            conn = pool.getconn()
            with conn.cursor() as cursor:
                metadata_payload = event.get("metadata") or {}
                if not isinstance(metadata_payload, dict):
                    metadata_payload = {}

                tenant_id = str(event.get("tenant_id") or metadata_payload.get("tenant_id") or "1").strip() or "1"
                event_type = str(event.get("event_type") or "").strip().lower() or "movement"
                confidence = event.get("confidence")
                if confidence is None:
                    confidence = metadata_payload.get("confidence")
                try:
                    confidence_value = float(confidence) if confidence is not None else None
                except (TypeError, ValueError):
                    confidence_value = None

                has_intrusion = bool(event_type == "intrusion" or metadata_payload.get("intrusion", False))
                # Treat any stored detection/ROI event as movement-activity by default.
                has_movement = bool(metadata_payload.get("has_movement", True))

                class_label = str(metadata_payload.get("class_label") or "").strip().lower()
                classes_value = metadata_payload.get("classes")
                if isinstance(classes_value, list):
                    classes_payload = [str(item).strip().lower() for item in classes_value if str(item).strip()]
                else:
                    classes_payload = [class_label] if class_label else []

                raw_event_payload = metadata_payload.get("frame_event")
                if not isinstance(raw_event_payload, dict):
                    raw_event_payload = event.get("raw_event") if isinstance(event.get("raw_event"), dict) else {}

                frame_number = metadata_payload.get("frame_number")
                try:
                    frame_number_value = int(frame_number) if frame_number is not None else None
                except (TypeError, ValueError):
                    frame_number_value = None

                cursor.execute(
                    """
                    INSERT INTO roi_events (
                        id,
                        tenant_id,
                        camera_id,
                        timestamp,
                        event_type,
                        zone,
                        confidence,
                        frame_number,
                        has_intrusion,
                        has_movement,
                        classes,
                        raw_event,
                        metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(uuid.uuid4()),
                        tenant_id,
                        event.get("camera_id"),
                        event.get("timestamp"),
                        event_type,
                        event.get("zone"),
                        confidence_value,
                        frame_number_value,
                        has_intrusion,
                        has_movement,
                        Json(classes_payload),
                        Json(raw_event_payload),
                        Json(metadata_payload),
                    ),
                )
            conn.commit()
            return True
        except Exception as exc:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass

            now = time.monotonic()
            if (now - self._last_insert_error_log) >= 30:
                logger.warning("Failed to insert ROI event into PostgreSQL: %s", exc)
                self._last_insert_error_log = now
            return False
        finally:
            if conn is not None:
                pool.putconn(conn)


_ROI_EVENT_PG_WRITER = _RoiEventPostgresWriter()
atexit.register(_ROI_EVENT_PG_WRITER.close)


def insert_roi_event(event: dict) -> bool:
    """
    Insert one ROI event into PostgreSQL.

    Expected keys:
      - camera_id
      - timestamp
      - event_type
      - zone
      - confidence
      - metadata
    """
    return _ROI_EVENT_PG_WRITER.insert_roi_event(event)


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_log_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return _backend_root() / path


def _load_allowed_classes() -> set[str]:
    settings = get_settings()
    configured = {
        str(item).strip().lower()
        for item in settings.ALLOWED_CLASSES
        if str(item).strip()
    }
    if not configured:
        configured = {
            str(item).strip().lower()
            for item in settings.MONITORED_CLASSES
            if str(item).strip()
        }
    return configured or set(_DEFAULT_ALLOWED_CLASSES)


ALLOWED_CLASSES = _load_allowed_classes()
# Backward-compat export alias.
MONITORED_CLASSES = ALLOWED_CLASSES


class JsonEventLogger:
    """Append-only JSON-lines logger for detection events."""

    def __init__(self, path: str):
        self._path = path
        directory = os.path.dirname(path)
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

    def log(self, event: Dict) -> None:
        try:
            with open(self._path, "a", encoding="utf-8") as handle:
                json.dump(event, handle, ensure_ascii=False)
                handle.write("\n")
        except Exception:
            # Logging should not break realtime processing.
            pass


class RoiEventReporter:
    """Persist ROI events to DB (default) with optional legacy JSONL output."""

    def __init__(self):
        settings = get_settings()
        self._allowed_classes = set(ALLOWED_CLASSES)
        self._last_logged_frame: dict[str, int] = {}
        self._write_db = bool(settings.ROI_EVENTS_WRITE_DB)
        self._db_write_error_logged = False

    def reset(self, camera_id: Optional[str] = None) -> None:
        if camera_id is None:
            self._last_logged_frame.clear()
            return
        self._last_logged_frame.pop(str(camera_id), None)

    def record_detections(
        self,
        camera_id: str,
        tracked_objects: List[Dict],
        intrusion_events: Optional[list[IntrusionEvent]] = None,
        timestamp: Optional[datetime] = None,
        tenant_id: str = "1",
    ) -> None:
        if not tracked_objects:
            return

        camera_id_str = str(camera_id)
        valid_objects: list[dict] = []
        for obj in tracked_objects:
            class_label = str(obj.get("class_label", "")).strip().lower()
            if class_label in self._allowed_classes:
                valid_objects.append(obj)

        if not valid_objects:
            return

        frame_number = max(int(obj.get("frame_number", 0)) for obj in valid_objects)
        last_frame = self._last_logged_frame.get(camera_id_str)
        if last_frame is not None and frame_number == last_frame:
            logger.debug(
                "Skipping duplicate frame-level event write for camera=%s frame=%s",
                camera_id_str,
                frame_number,
            )
            return
        self._last_logged_frame[camera_id_str] = frame_number

        intrusion_map: dict[tuple[int, int], list[IntrusionEvent]] = {}
        for intr in intrusion_events or []:
            key = (int(intr.zone_id), int(intr.object_id))
            intrusion_map.setdefault(key, []).append(intr)

        detection_payload: list[dict] = []
        for obj in sorted(valid_objects, key=lambda item: int(item.get("object_id", -1))):
            class_label = str(obj.get("class_label", "")).strip().lower()
            object_id = int(obj.get("object_id", -1))
            zone_ids = sorted({int(z) for z in obj.get("roi_zone_ids", [])})
            per_object_intrusions: list[dict] = []
            for zone_id in zone_ids:
                for intr in intrusion_map.get((zone_id, object_id), []):
                    per_object_intrusions.append(
                        {
                            "zone_id": int(intr.zone_id),
                            "zone_name": str(intr.zone_name),
                            "dwell_seconds": round(float(intr.dwell_seconds), 2),
                            "threshold_seconds": round(float(intr.threshold_seconds), 2),
                        }
                    )

            detection_payload.append(
                {
                    "object_id": object_id,
                    "class_id": int(_COCO_ALLOWED_CLASS_ID.get(class_label, -1)),
                    "class_label": class_label,
                    "confidence": round(float(obj.get("confidence", 0.0)), 4),
                    "bbox": [
                        float(obj.get("bbox_x", 0.0)),
                        float(obj.get("bbox_y", 0.0)),
                        float(obj.get("bbox_w", 0.0)),
                        float(obj.get("bbox_h", 0.0)),
                    ],
                    "inside_roi": bool(obj.get("inside_roi", False)),
                    "intrusion": bool(obj.get("intrusion", False)),
                    "roi_zone_ids": zone_ids,
                    "max_roi_dwell_sec": round(float(obj.get("max_roi_dwell_sec", 0.0)), 2),
                    "intrusion_events": per_object_intrusions,
                }
            )

        event_time = timestamp or utc_now()
        frame_event = {
            "timestamp": event_time.isoformat() + "Z",
            "camera_id": camera_id_str,
            "frame_number": frame_number,
            "detections": detection_payload,
        }

        if self._write_db:
            self._write_events_to_db(
                camera_id=camera_id_str,
                event_time=event_time,
                frame_number=frame_number,
                detections=detection_payload,
                frame_event=frame_event,
                tenant_id=tenant_id,
            )

    def _write_events_to_db(
        self,
        camera_id: str,
        event_time: datetime,
        frame_number: int,
        detections: list[dict],
        frame_event: dict,
        tenant_id: str = "1",
    ) -> None:
        for detection in detections:
            has_intrusion = bool(detection.get("intrusion", False))
            event_type = "intrusion" if has_intrusion else "movement"
            confidence = float(detection.get("confidence", 0.0))

            zone_names = sorted(
                {
                    str(item.get("zone_name", "")).strip()
                    for item in detection.get("intrusion_events", [])
                    if isinstance(item, dict) and str(item.get("zone_name", "")).strip()
                }
            )
            if not zone_names:
                zone_names = [str(zone_id) for zone_id in detection.get("roi_zone_ids", [])]
                zone_names = [name for name in zone_names if name]

            metadata_payload = {
                "camera_id": camera_id,
                "frame_number": frame_number,
                "object_id": int(detection.get("object_id", -1)),
                "class_id": int(detection.get("class_id", -1)),
                "class_label": str(detection.get("class_label", "unknown")).strip().lower() or "unknown",
                "confidence": confidence,
                "bbox": detection.get("bbox", []),
                "inside_roi": bool(detection.get("inside_roi", False)),
                "intrusion": has_intrusion,
                "roi_zone_ids": detection.get("roi_zone_ids", []),
                "roi_zone_names": zone_names,
                "max_roi_dwell_sec": float(detection.get("max_roi_dwell_sec", 0.0)),
                "intrusion_events": detection.get("intrusion_events", []),
                "frame_event": frame_event,
            }

            event_payload = {
                "camera_id": camera_id,
                "timestamp": event_time,
                "event_type": event_type,
                "zone": zone_names[0] if zone_names else None,
                "confidence": confidence,
                "metadata": metadata_payload,
                "tenant_id": tenant_id,
            }

            ok = insert_roi_event(event_payload)
            if not ok and not self._db_write_error_logged:
                # Keep processing detections; DB failures are non-fatal.
                logger.warning("ROI event insert failed; continuing detection pipeline.")
                self._db_write_error_logged = True
