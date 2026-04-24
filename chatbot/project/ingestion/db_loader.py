from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from project.ingestion.event_loader import flatten_frame_record
from project.settings import LEGACY_POSTGRES_CHECKPOINT_PATH, POSTGRES_CHECKPOINT_PATH


logger = logging.getLogger(__name__)

_ENGINE_CACHE: dict[str, Engine] = {}


def default_db_cursor() -> dict[str, str]:
    return {
        "timestamp": "1970-01-01T00:00:00+00:00",
        "id": "",
    }


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=path.parent) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, path)


def _normalize_cursor(payload: Any) -> dict[str, str]:
    default = default_db_cursor()
    if not isinstance(payload, dict):
        return default

    timestamp = str(payload.get("timestamp", "")).strip()
    cursor_id = str(payload.get("id", "")).strip()
    if not timestamp:
        timestamp = default["timestamp"]
    return {"timestamp": timestamp, "id": cursor_id}


def _read_checkpoint_file(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None

    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid DB checkpoint JSON in %s. Resetting to default cursor.", path)
        return None
    return _normalize_cursor(payload)


def read_db_checkpoint(checkpoint_path: str | Path = POSTGRES_CHECKPOINT_PATH) -> dict[str, str]:
    checkpoint_file = Path(checkpoint_path)
    cursor = _read_checkpoint_file(checkpoint_file)
    if cursor is not None:
        return cursor

    default_checkpoint = Path(POSTGRES_CHECKPOINT_PATH)
    if checkpoint_file == default_checkpoint:
        legacy_file = Path(LEGACY_POSTGRES_CHECKPOINT_PATH)
        legacy_cursor = _read_checkpoint_file(legacy_file)
        if legacy_cursor is not None:
            write_db_checkpoint(legacy_cursor, checkpoint_file)
            logger.info("Migrated legacy checkpoint from %s to %s", legacy_file, checkpoint_file)
            return legacy_cursor

    return default_db_cursor()


def write_db_checkpoint(
    cursor: dict[str, str],
    checkpoint_path: str | Path = POSTGRES_CHECKPOINT_PATH,
) -> None:
    normalized = _normalize_cursor(cursor)
    payload = json.dumps(normalized, separators=(",", ":"))
    _atomic_write_text(Path(checkpoint_path), payload)


def _get_engine(database_url: str) -> Engine:
    cached = _ENGINE_CACHE.get(database_url)
    if cached is not None:
        return cached

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    _ENGINE_CACHE[database_url] = engine
    return engine


def _parse_cursor_ts(timestamp: str) -> datetime:
    text_value = str(timestamp).strip()
    if not text_value:
        return datetime(1970, 1, 1)

    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError:
        return datetime(1970, 1, 1)

    # DB column roi_events.timestamp is timestamp without time zone.
    # Keep cursor naive to avoid session timezone conversions that can skip rows.
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _normalize_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    text_value = str(value).strip()
    if not text_value:
        return default_db_cursor()["timestamp"]

    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except ValueError:
        return text_value


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return None
        try:
            decoded = json.loads(text_value)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, dict):
            return decoded
    return None


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text_value = str(item).strip()
        if text_value:
            normalized.append(text_value)
    return normalized


def _build_event_from_db_row(row: dict[str, Any], row_id: str, row_ts: str) -> dict[str, Any] | None:
    metadata_payload = _coerce_json_object(row.get("metadata")) or {}
    # Legacy fallback rows may only carry raw_event. Let the raw_event parser handle those.
    if not metadata_payload and row.get("camera_id") is None and row.get("raw_event") is not None:
        return None

    camera_id = str(row.get("camera_id") or metadata_payload.get("camera_id") or "unknown").strip() or "unknown"
    event_type = str(row.get("event_type") or "").strip().lower()
    zone_value = str(row.get("zone") or "").strip()
    zone_names = _normalize_string_list(metadata_payload.get("roi_zone_names"))
    if not zone_names and zone_value:
        zone_names = [zone_value]

    zone_ids = _normalize_string_list(metadata_payload.get("roi_zone_ids"))
    class_label = str(
        row.get("class_label") or metadata_payload.get("class_label") or ""
    ).strip().lower()

    # The classes column may be a list ["person"] or a dict {"person": 4, "vehicle": 2}.
    raw_classes = row.get("classes") or metadata_payload.get("classes")
    if isinstance(raw_classes, dict):
        classes = [str(k).strip().lower() for k, v in raw_classes.items() if str(k).strip() and v]
    elif isinstance(raw_classes, list):
        classes = [str(item).strip().lower() for item in raw_classes if str(item).strip()]
    else:
        classes = []

    if not classes and class_label:
        classes = [class_label]
    if not class_label and classes:
        class_label = classes[0]
    if not class_label:
        class_label = "unknown"

    intrusion = bool(metadata_payload.get("intrusion", event_type == "intrusion"))
    has_intrusion = bool(row.get("has_intrusion", intrusion))
    has_movement = bool(row.get("has_movement", True))
    if event_type == "intrusion":
        has_intrusion = True

    return {
        "event_id": row_id,
        "timestamp": row_ts,
        "camera_id": camera_id,
        "frame_number": _safe_int(
            row.get("frame_number", metadata_payload.get("frame_number")), default=-1
        ),
        "object_id": _safe_int(
            row.get("object_id", metadata_payload.get("object_id")), default=-1
        ),
        "class_id": _safe_int(
            row.get("class_id", metadata_payload.get("class_id")), default=-1
        ),
        "class_label": class_label,
        "classes": classes,
        "confidence": _safe_float(
            row.get("confidence", metadata_payload.get("confidence")), default=0.0
        ),
        "bbox": metadata_payload.get("bbox", []),
        "inside_roi": bool(metadata_payload.get("inside_roi", False)),
        "intrusion": has_intrusion or intrusion,
        "has_intrusion": has_intrusion or intrusion,
        "has_movement": has_movement,
        "event_type": event_type or ("intrusion" if (has_intrusion or intrusion) else "movement"),
        "roi_zone_ids": zone_ids,
        "roi_zone_names": zone_names,
        "max_roi_dwell_sec": _safe_float(metadata_payload.get("max_roi_dwell_sec"), default=0.0),
    }


def load_events_from_db(
    database_url: str,
    tenant_id: str,
    checkpoint_path: str | Path = POSTGRES_CHECKPOINT_PATH,
    max_rows: int = 2000,
    start_cursor: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], int]:
    if max_rows <= 0:
        cursor = _normalize_cursor(start_cursor if start_cursor is not None else read_db_checkpoint(checkpoint_path))
        return [], cursor, 0

    persisted_cursor = read_db_checkpoint(checkpoint_path)
    cursor = _normalize_cursor(start_cursor if start_cursor is not None else persisted_cursor)
    cursor_ts = _parse_cursor_ts(cursor["timestamp"])
    cursor_id = cursor["id"]

    engine = _get_engine(database_url)
    query = text(
        """
        SELECT
            id,
            timestamp,
            camera_id,
            event_type,
            zone,
            metadata,
            raw_event,
            has_intrusion,
            has_movement,
            classes,
            frame_number,
            confidence
        FROM roi_events
        WHERE tenant_id = :tenant_id
          AND (
            timestamp > :cursor_ts
            OR (timestamp = :cursor_ts AND id > :cursor_id)
          )
        ORDER BY timestamp ASC, id ASC
        LIMIT :limit_rows
        """
    )
    legacy_query = text(
        """
        SELECT id, timestamp, raw_event
        FROM roi_events
        WHERE tenant_id = :tenant_id
          AND (
            timestamp > :cursor_ts
            OR (timestamp = :cursor_ts AND id > :cursor_id)
          )
        ORDER BY timestamp ASC, id ASC
        LIMIT :limit_rows
        """
    )

    with engine.connect() as conn:
        params = {
            "tenant_id": tenant_id,
            "cursor_ts": cursor_ts,
            "cursor_id": cursor_id,
            "limit_rows": int(max_rows),
        }
        try:
            result = conn.execute(query, params)
        except Exception:
            logger.warning("Falling back to legacy roi_events schema query (raw_event only).")
            conn.rollback()
            result = conn.execute(legacy_query, params)
        rows = result.mappings().all()

    parsed_events: list[dict[str, Any]] = []
    next_cursor = dict(cursor)

    for row in rows:
        row_id = str(row.get("id", "")).strip()
        row_ts = _normalize_timestamp(row.get("timestamp"))

        event = _build_event_from_db_row(row, row_id=row_id, row_ts=row_ts)
        if event is not None:
            parsed_events.append(event)
            next_cursor = {"timestamp": row_ts, "id": row_id}
            continue

        record = _coerce_json_object(row.get("raw_event"))
        if record is None:
            logger.warning("Skipping roi_events row id=%s due to invalid payload", row_id or "unknown")
            next_cursor = {"timestamp": row_ts, "id": row_id}
            continue

        if not record.get("timestamp"):
            record["timestamp"] = row_ts

        parsed_events.extend(flatten_frame_record(record, source_offset=0))
        next_cursor = {"timestamp": row_ts, "id": row_id}

    return parsed_events, next_cursor, len(rows)
