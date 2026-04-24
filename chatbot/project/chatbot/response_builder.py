from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any


def parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def event_classes(event: dict[str, Any]) -> list[str]:
    classes_value = event.get("classes")
    if isinstance(classes_value, dict):
        classes = [str(k).strip().lower() for k, v in classes_value.items() if str(k).strip() and v]
        if classes:
            return classes
    if isinstance(classes_value, list):
        classes = [str(value).strip().lower() for value in classes_value if str(value).strip()]
        if classes:
            return classes

    class_label = str(event.get("class_label", "")).strip().lower()
    return [class_label] if class_label else []


def event_zone(event: dict[str, Any]) -> str | None:
    zone_names = event.get("roi_zone_names")
    if isinstance(zone_names, list):
        normalized = [str(value).strip() for value in zone_names if str(value).strip()]
        if normalized:
            return ", ".join(normalized)

    zone_ids = event.get("roi_zone_ids")
    if isinstance(zone_ids, list):
        normalized = [str(value).strip() for value in zone_ids if str(value).strip()]
        if normalized:
            return ", ".join(normalized)
    return None


def _known_class_counts(events: list[dict[str, Any]]) -> Counter[str]:
    counter: Counter[str] = Counter()
    for event in events:
        for class_name in event_classes(event):
            if class_name and class_name != "unknown":
                counter[class_name] += 1
    return counter


def _top_counts_text(counter: Counter[str], limit: int = 4) -> str:
    if not counter:
        return "object classes were not reliably labeled"
    return ", ".join(f"{label}={count}" for label, count in counter.most_common(limit))


def _time_span_text(events: list[dict[str, Any]]) -> str:
    timestamps = [timestamp for timestamp in (parse_timestamp(event.get("timestamp")) for event in events) if timestamp is not None]
    if not timestamps:
        return "unknown time span"
    return f"{min(timestamps).isoformat()} to {max(timestamps).isoformat()}"


def _camera_stats(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for event in events:
        camera_id = str(event.get("camera_id", "unknown")).strip() or "unknown"
        item = stats.setdefault(
            camera_id,
            {
                "events": 0,
                "movement": 0,
                "intrusions": 0,
                "latest": None,
                "zones": set(),
                "classes": Counter(),
            },
        )
        item["events"] += 1
        if bool(event.get("has_movement", True)):
            item["movement"] += 1
        if bool(event.get("has_intrusion", event.get("intrusion", False))):
            item["intrusions"] += 1
        timestamp = parse_timestamp(event.get("timestamp"))
        if timestamp is not None and (item["latest"] is None or timestamp > item["latest"]):
            item["latest"] = timestamp
        zone_name = event_zone(event)
        if zone_name:
            item["zones"].add(zone_name)
        for class_name in event_classes(event):
            if class_name and class_name != "unknown":
                item["classes"][class_name] += 1
    return stats


def build_camera_inventory_answer(events: list[dict[str, Any]], detailed: bool = False) -> str:
    stats = _camera_stats(events)
    if not stats:
        return "I could not determine any cameras from the available activity logs."

    camera_ids = sorted(stats)
    if not detailed:
        preview = ", ".join(camera_ids[:5])
        remaining = len(camera_ids) - min(len(camera_ids), 5)
        suffix = f", and {remaining} more" if remaining > 0 else ""
        return f"I found {len(camera_ids)} cameras with logged activity: {preview}{suffix}."

    details = []
    for camera_id in camera_ids[:5]:
        item = stats[camera_id]
        latest = item["latest"].isoformat() if item["latest"] is not None else "unknown"
        details.append(
            f"{camera_id}: {item['events']} events, {item['intrusions']} intrusions, latest {latest}"
        )
    return f"I found {len(camera_ids)} cameras with logged activity. " + "; ".join(details) + "."


def build_movement_summary_answer(events: list[dict[str, Any]], detailed: bool = False) -> str:
    movement_events = [event for event in events if bool(event.get("has_movement", True))]
    if not movement_events:
        return "No movement activity matching the request was found."

    camera_count = len(_camera_stats(movement_events))
    intrusion_count = sum(1 for event in movement_events if bool(event.get("has_intrusion", event.get("intrusion", False))))
    class_counts = _known_class_counts(movement_events)
    answer = (
        f"I found {len(movement_events)} movement events across {camera_count} cameras. "
        f"Intrusions among them: {intrusion_count}. "
        f"Object counts: {_top_counts_text(class_counts)}."
    )
    if detailed:
        answer += f" Time span: {_time_span_text(movement_events)}."
    return answer


def build_intrusion_summary_answer(events: list[dict[str, Any]], detailed: bool = False) -> str:
    intrusion_events = [event for event in events if bool(event.get("has_intrusion", event.get("intrusion", False)))]
    if not intrusion_events:
        return "No intrusion activity matching the request was found."

    camera_count = len(_camera_stats(intrusion_events))
    class_counts = _known_class_counts(intrusion_events)
    answer = (
        f"I found {len(intrusion_events)} intrusion events across {camera_count} cameras. "
        f"Object counts: {_top_counts_text(class_counts)}."
    )
    if detailed:
        answer += f" Time span: {_time_span_text(intrusion_events)}."
    return answer


def build_activity_overview_answer(events: list[dict[str, Any]], detailed: bool = False) -> str:
    if not events:
        return "No activity matching the request was found."

    camera_stats = _camera_stats(events)
    intrusion_count = sum(1 for event in events if bool(event.get("has_intrusion", event.get("intrusion", False))))
    class_counts = _known_class_counts(events)
    answer = (
        f"I found {len(events)} matching events across {len(camera_stats)} cameras. "
        f"Intrusions: {intrusion_count}. "
        f"Object counts: {_top_counts_text(class_counts)}."
    )
    if detailed:
        top_cameras = ", ".join(
            f"{camera_id}={item['events']}"
            for camera_id, item in sorted(camera_stats.items(), key=lambda pair: (-pair[1]['events'], pair[0]))[:4]
        )
        answer += f" Top cameras: {top_cameras or 'none'}. Time span: {_time_span_text(events)}."
    return answer


def build_follow_up_answer(context: dict[str, Any]) -> str | None:
    mode = str(context.get("mode", "")).strip()
    events = context.get("events")
    if not isinstance(events, list) or not events:
        return None

    if mode == "camera_inventory":
        return build_camera_inventory_answer(events, detailed=True)
    if mode == "movement_summary":
        return build_movement_summary_answer(events, detailed=True)
    if mode == "intrusion_summary":
        return build_intrusion_summary_answer(events, detailed=True)
    return build_activity_overview_answer(events, detailed=True)
