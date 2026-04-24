from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from project.settings import CHECKPOINT_PATH, ROI_EVENTS_PATH


logger = logging.getLogger(__name__)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", dir=path.parent) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, path)


def _line_to_byte_offset(line_checkpoint: int, jsonl_path: Path) -> int:
    if line_checkpoint <= 0 or not jsonl_path.exists():
        return 0

    offset = 0
    with jsonl_path.open("rb") as handle:
        for _ in range(line_checkpoint):
            line = handle.readline()
            if not line:
                break
            offset = handle.tell()
    return max(offset, 0)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_bool(value: Any) -> bool:
    return bool(value)


def _normalize_zone_ids(raw_zone_ids: Any) -> list[str]:
    if not isinstance(raw_zone_ids, list):
        return []
    normalized: list[str] = []
    for zone in raw_zone_ids:
        text = str(zone).strip()
        if text:
            normalized.append(text)
    return normalized


def _extract_zone_names(intrusion_events: Any) -> list[str]:
    if not isinstance(intrusion_events, list):
        return []
    names: list[str] = []
    for event in intrusion_events:
        if not isinstance(event, dict):
            continue
        zone_name = str(event.get("zone_name", "")).strip()
        if zone_name:
            names.append(zone_name)
    return sorted(set(names))


def _build_event_id(
    camera_id: str,
    timestamp: str,
    frame_number: int,
    object_id: int,
    class_label: str,
    zone_ids: list[str],
    detection_index: int,
) -> str:
    payload = "|".join(
        (
            camera_id,
            timestamp,
            str(frame_number),
            str(object_id),
            class_label,
            ",".join(zone_ids),
            str(detection_index),
        )
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


def flatten_frame_record(record: dict[str, Any], source_offset: int = 0) -> list[dict[str, Any]]:
    timestamp = str(record.get("timestamp", "")).strip()
    camera_id = str(record.get("camera_id", "unknown")).strip() or "unknown"
    frame_number = _safe_int(record.get("frame_number"), default=-1)

    detections = record.get("detections")
    if not isinstance(detections, list):
        logger.warning(
            "Skipping frame record at source offset %s: 'detections' is not a list",
            source_offset,
        )
        return []

    flattened: list[dict[str, Any]] = []
    for detection_index, detection in enumerate(detections):
        if not isinstance(detection, dict):
            continue

        class_label = str(detection.get("class_label", "unknown")).strip().lower() or "unknown"
        object_id = _safe_int(detection.get("object_id"), default=-1)
        zone_ids = _normalize_zone_ids(detection.get("roi_zone_ids"))
        zone_names = _extract_zone_names(detection.get("intrusion_events"))
        event_id = _build_event_id(
            camera_id=camera_id,
            timestamp=timestamp,
            frame_number=frame_number,
            object_id=object_id,
            class_label=class_label,
            zone_ids=zone_ids,
            detection_index=detection_index,
        )

        flattened.append(
            {
                "event_id": event_id,
                "timestamp": timestamp,
                "camera_id": camera_id,
                "frame_number": frame_number,
                "object_id": object_id,
                "class_id": _safe_int(detection.get("class_id"), default=-1),
                "class_label": class_label,
                "classes": [class_label] if class_label else [],
                "confidence": _safe_float(detection.get("confidence"), default=0.0),
                "bbox": detection.get("bbox", []),
                "inside_roi": _to_bool(detection.get("inside_roi")),
                "intrusion": _to_bool(detection.get("intrusion")),
                "has_intrusion": _to_bool(detection.get("intrusion")),
                "has_movement": True,
                "roi_zone_ids": zone_ids,
                "roi_zone_names": zone_names,
                "max_roi_dwell_sec": _safe_float(detection.get("max_roi_dwell_sec"), default=0.0),
            }
        )
    return flattened


def read_checkpoint(
    checkpoint_path: str | Path = CHECKPOINT_PATH,
    jsonl_path: str | Path = ROI_EVENTS_PATH,
) -> int:
    """
    Read ingestion checkpoint as byte offset.

    Checkpoint formats:
    - JSON: {"offset": 1234}
    - Legacy plain integer: interpreted as line count and migrated to byte offset.
    """
    checkpoint_file = Path(checkpoint_path)
    if not checkpoint_file.exists():
        return 0

    raw = checkpoint_file.read_text(encoding="utf-8").strip()
    if not raw:
        return 0

    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
            return max(_safe_int(payload.get("offset"), default=0), 0)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid checkpoint JSON in %s. Resetting to 0.", checkpoint_file)
            return 0

    try:
        line_checkpoint = int(raw)
    except ValueError:
        logger.warning("Invalid checkpoint value in %s. Resetting to 0.", checkpoint_file)
        return 0

    offset = _line_to_byte_offset(line_checkpoint, Path(jsonl_path))
    write_checkpoint(offset, checkpoint_file)
    logger.info(
        "Migrated legacy line checkpoint to byte offset: line=%s offset=%s",
        line_checkpoint,
        offset,
    )
    return offset


def write_checkpoint(last_processed_offset: int, checkpoint_path: str | Path = CHECKPOINT_PATH) -> None:
    offset = max(_safe_int(last_processed_offset, default=0), 0)
    payload = json.dumps({"offset": offset}, separators=(",", ":"))
    _atomic_write_text(Path(checkpoint_path), payload)


def load_events_from_jsonl(
    jsonl_path: str | Path = ROI_EVENTS_PATH,
    checkpoint_path: str | Path = CHECKPOINT_PATH,
    max_lines: int = 2000,
    start_offset: int = 0,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Incrementally load and parse newly appended JSONL frame events.

    Returns:
    - parsed events (flattened to one entry per detection)
    - new byte offset
    - processed line count
    """
    if max_lines <= 0:
        initial_offset = max(_safe_int(start_offset, default=0), 0)
        return [], initial_offset, 0

    source = Path(jsonl_path)
    checkpoint_offset = read_checkpoint(checkpoint_path, source)
    requested_offset = max(_safe_int(start_offset, default=0), 0)
    # Caller-managed offsets take precedence once ingestion has started.
    start = requested_offset if requested_offset > 0 else checkpoint_offset

    if not source.exists():
        return [], start, 0

    file_size = source.stat().st_size
    if start > file_size:
        logger.warning(
            "Start offset %s is past end of file (%s) for %s. Resetting to 0.",
            start,
            file_size,
            source,
        )
        start = 0

    parsed_events: list[dict[str, Any]] = []
    processed_lines = 0
    new_offset = start

    with source.open("rb") as handle:
        handle.seek(start)
        while processed_lines < max_lines:
            line_start = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break

            line_end = handle.tell()
            if not raw_line.endswith(b"\n") and line_end >= file_size:
                # Writer has not completed this line yet; retry from line start next cycle.
                handle.seek(line_start)
                new_offset = line_start
                break

            processed_lines += 1
            new_offset = line_end

            try:
                line_text = raw_line.decode("utf-8").strip()
            except UnicodeDecodeError:
                logger.warning("Skipping undecodable JSONL line ending at byte offset %s", line_end)
                continue

            if not line_text:
                continue

            try:
                frame_record = json.loads(line_text)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSONL line ending at byte offset %s", line_end)
                continue

            if not isinstance(frame_record, dict):
                continue

            parsed_events.extend(flatten_frame_record(frame_record, source_offset=line_end))

    return parsed_events, new_offset, processed_lines
