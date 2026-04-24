from __future__ import annotations

import hashlib
from typing import Any


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


def _format_duration(seconds: float) -> str:
    rounded = max(0.0, _safe_float(seconds, default=0.0))
    if rounded >= 10:
        return f"{rounded:.0f}"
    if rounded >= 1:
        return f"{rounded:.1f}"
    return f"{rounded:.2f}"


def _zone_list(event: dict[str, Any]) -> list[str]:
    zone_names = event.get("roi_zone_names")
    if isinstance(zone_names, list):
        normalized = [str(name).strip() for name in zone_names if str(name).strip()]
        if normalized:
            return normalized

    zone_ids = event.get("roi_zone_ids")
    if not isinstance(zone_ids, list):
        return []
    return [str(zone).strip() for zone in zone_ids if str(zone).strip()]


def _event_type(event: dict[str, Any]) -> str:
    return "intrusion" if bool(event.get("intrusion", False)) else "movement"


def _event_id(event: dict[str, Any]) -> str:
    raw = str(event.get("event_id", "")).strip()
    if raw:
        return raw

    payload = "|".join(
        (
            str(event.get("camera_id", "unknown")),
            str(event.get("timestamp", "")),
            str(_safe_int(event.get("frame_number"), default=-1)),
            str(_safe_int(event.get("object_id"), default=-1)),
            str(event.get("class_label", "unknown")),
            ",".join(_zone_list(event)),
        )
    )
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


def build_document_from_event(event: dict[str, Any]) -> str:
    camera_id = str(event.get("camera_id", "unknown")).strip() or "unknown"
    timestamp = str(event.get("timestamp", "unknown")).strip() or "unknown"
    class_label = str(event.get("class_label", "object")).strip().lower() or "object"
    dwell_seconds = _safe_float(event.get("max_roi_dwell_sec", 0.0), default=0.0)
    zones = _zone_list(event)
    zone_text = ", ".join(zones) if zones else "unassigned zone"
    is_intrusion = bool(event.get("intrusion", False))
    inside_roi = bool(event.get("inside_roi", False))

    if is_intrusion:
        return (
            f"Camera {camera_id} detected a {class_label} inside {zone_text} zone for "
            f"{_format_duration(dwell_seconds)} seconds at {timestamp}. Event type: intrusion."
        )
    if inside_roi:
        return (
            f"Camera {camera_id} observed a {class_label} moving inside {zone_text} zone at "
            f"{timestamp}. Event type: movement."
        )
    return (
        f"Camera {camera_id} observed a {class_label} outside ROI zones at {timestamp}. "
        "Event type: movement."
    )


def build_documents_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for event in events:
        event_id = _event_id(event)
        zones = _zone_list(event)
        zone_text = ", ".join(zones) if zones else "none"

        documents.append(
            {
                "id": event_id,
                "text": build_document_from_event(event),
                "metadata": {
                    "event_id": event_id,
                    "camera_id": str(event.get("camera_id", "unknown")),
                    "timestamp": str(event.get("timestamp", "unknown")),
                    "zone": zone_text,
                    "zones": zones,
                    "event_type": _event_type(event),
                    "frame_number": _safe_int(event.get("frame_number"), default=-1),
                    "object_id": _safe_int(event.get("object_id"), default=-1),
                    "class_label": str(event.get("class_label", "unknown")),
                    "intrusion": bool(event.get("intrusion", False)),
                    "inside_roi": bool(event.get("inside_roi", False)),
                    "max_roi_dwell_sec": _safe_float(event.get("max_roi_dwell_sec", 0.0), default=0.0),
                },
                "event": event,
            }
        )
    return documents
