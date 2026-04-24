from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Any

from project.chatbot.response_builder import parse_timestamp as _parse_iso_datetime  # canonical shared impl


OBJECT_CLASS_ALIASES: dict[str, tuple[str, ...]] = {
    "person": ("person", "persons", "people", "human"),
    "car": ("car", "cars", "vehicle", "vehicles"),
    "bike": ("bike", "bikes", "bicycle", "bicycles", "motorbike", "motorcycle"),
    "bus": ("bus", "buses"),
    "truck": ("truck", "trucks"),
}

EVENT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "movement": ("movement", "movements", "motion", "moving", "moved"),
    "intrusion": ("intrusion", "intrusions", "intruder", "intrution", "breach", "entered", "entry"),
}

RECENCY_KEYWORDS = ("recent", "latest", "newest", "last seen", "most recent", "currently")
ANOMALY_KEYWORDS = ("unusual", "anything unusual", "anomaly", "abnormal", "suspicious", "odd", "strange")
PATTERN_KEYWORDS = ("repeated", "repeat", "pattern", "again and again", "multiple times", "recurring", "frequent")
FOLLOW_UP_PHRASES = (
    "tell me about it",
    "tell me more",
    "more detail",
    "more details",
    "more info",
    "explain it",
    "explain that",
    "what about it",
    "what about them",
    "expand on that",
)

TREND_KEYWORDS = (
    "increase", "increased", "increasing", "decrease", "decreased", "decreasing",
    "more than usual", "growing", "rising", "more often", "trending", "spike",
)

# (start_hour, end_hour) — end_hour < start_hour means wrap-around past midnight
_TIME_OF_DAY_RANGES: dict[str, tuple[int, int]] = {
    "late at night": (22, 4),
    "late night": (22, 4),
    "early morning": (4, 8),
    "early in the morning": (4, 8),
    "overnight": (21, 6),
    "midnight": (23, 1),
    "dawn": (5, 8),
    "sunrise": (5, 8),
    "morning": (6, 12),
    "midday": (11, 13),
    "noon": (11, 13),
    "afternoon": (12, 18),
    "evening": (17, 22),
    "dusk": (17, 20),
    "sunset": (17, 20),
    "night": (20, 4),
    "at night": (20, 4),
}

_MONTH_NAMES: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_WEEKDAY_NAMES: dict[str, int] = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

# Module-level compiled patterns — avoids recompilation on every call
_TIME_OF_DAY_SORTED: list[tuple[str, tuple[int, int]]] = sorted(
    _TIME_OF_DAY_RANGES.items(), key=lambda x: -len(x[0])
)

_RE_BETWEEN = re.compile(
    r"\bbetween\s+(\d{1,2})(?::\d{2})?\s*(am|pm)?\s*(?:and|to|-)\s*(\d{1,2})(?::\d{2})?\s*(am|pm)?\b"
)
_RE_RANGED = re.compile(
    r"\b(\d{1,2})(?::\d{2})?\s*(am|pm)?\s*(?:to|-)\s*(\d{1,2})(?::\d{2})?\s*(am|pm)?\b"
)
_RE_AFTER = re.compile(r"\bafter\s+(\d{1,2})(?::\d{2})?\s*(am|pm)\b")
_RE_BEFORE = re.compile(r"\bbefore\s+(\d{1,2})(?::\d{2})?\s*(am|pm)\b")
_RE_AT = re.compile(r"\bat\s+(\d{1,2})(?::\d{2})?\s*(am|pm)\b")
_RE_AT24 = re.compile(r"\bat\s+(\d{1,2}):(\d{2})\b")

_WEEKDAY_PATTERN = re.compile(
    r"\b(?:(?:last|this|on)\s+)?(" + "|".join(_WEEKDAY_NAMES.keys()) + r")\b"
)

_month_names_joined = "|".join(_MONTH_NAMES.keys())
_MONTH_PATTERN_FWD = re.compile(
    rf"\b({_month_names_joined})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:\s+(20\d{{2}}))?\b"
)
_MONTH_PATTERN_REV = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_month_names_joined})(?:\s+(20\d{{2}}))?\b"
)


def _to_24_hour(hour_text: str, meridiem: str | None) -> int | None:
    try:
        hour = int(hour_text)
    except ValueError:
        return None

    if meridiem is None:
        if 0 <= hour <= 23:
            return hour
        return None

    marker = meridiem.lower()
    if hour < 1 or hour > 12:
        return None
    if marker == "am":
        return 0 if hour == 12 else hour
    if marker == "pm":
        return 12 if hour == 12 else hour + 12
    return None


def _parse_time_of_day_label(query: str) -> tuple[int | None, int | None]:
    """Map natural language time-of-day phrases to (start_hour, end_hour).
    Checked longest-first so 'late at night' beats 'night'.
    end_hour < start_hour means the window wraps past midnight.
    """
    for label, (start, end) in _TIME_OF_DAY_SORTED:
        if re.search(rf"\b{re.escape(label)}\b", query):
            return start, end
    return None, None


def _parse_time_window(query: str) -> tuple[int | None, int | None]:
    # "between 2am and 4pm" / "between 14:00 and 16:00"
    between_match = _RE_BETWEEN.search(query)
    if between_match:
        start_meridiem = between_match.group(2) or between_match.group(4)
        end_meridiem = between_match.group(4) or between_match.group(2)
        start_hour = _to_24_hour(between_match.group(1), start_meridiem)
        end_hour = _to_24_hour(between_match.group(3), end_meridiem)
        if start_hour is not None and end_hour is not None:
            return start_hour, end_hour

    # "2pm to 4pm" / "14:00 - 16:00"
    ranged_match = _RE_RANGED.search(query)
    if ranged_match:
        start_marker = ranged_match.group(2)
        end_marker = ranged_match.group(4)
        if start_marker is None and end_marker is None:
            return None, None
        start_hour = _to_24_hour(ranged_match.group(1), start_marker or end_marker)
        end_hour = _to_24_hour(ranged_match.group(3), end_marker or start_marker)
        if start_hour is not None and end_hour is not None:
            return start_hour, end_hour

    # "after 10pm"
    after_match = _RE_AFTER.search(query)
    if after_match:
        start_hour = _to_24_hour(after_match.group(1), after_match.group(2))
        if start_hour is not None:
            return start_hour, 23

    # "before 6am"
    before_match = _RE_BEFORE.search(query)
    if before_match:
        end_hour = _to_24_hour(before_match.group(1), before_match.group(2))
        if end_hour is not None:
            return 0, end_hour

    # "at 3:15 pm" / "at 3pm" — narrow single-hour window
    at_match = _RE_AT.search(query)
    if at_match:
        hour = _to_24_hour(at_match.group(1), at_match.group(2))
        if hour is not None:
            return hour, hour

    # "at 14:30" or "at 14:00" (24-hour format with colon)
    at24_match = _RE_AT24.search(query)
    if at24_match:
        hour = _to_24_hour(at24_match.group(1), None)
        if hour is not None:
            return hour, hour

    return None, None


def _normalize_object_class(raw: str) -> str:
    lowered = raw.strip().lower()
    for canonical, aliases in OBJECT_CLASS_ALIASES.items():
        if lowered in aliases:
            return canonical
    return lowered


def _normalize_camera(raw: str) -> str:
    return re.sub(r"\s+", "-", raw.strip().lower())


def _normalize_token(raw: str) -> str:
    text = str(raw)
    return re.sub(r"\s+", " ", text.strip().lower())


def _camera_matches(document_camera: str, requested_cameras: set[str]) -> bool:
    if not requested_cameras:
        return True
    for requested in requested_cameras:
        if document_camera == requested:
            return True
        if document_camera.endswith(requested):
            return True
        if requested.endswith(document_camera):
            return True
        if requested in document_camera:
            return True
    return False


def _parse_weekday_range(query: str, now: datetime) -> tuple[datetime | None, datetime | None, str | None]:
    """Parse day-of-week references like 'last monday', 'on wednesday', 'this friday'."""
    match = _WEEKDAY_PATTERN.search(query)
    if not match:
        return None, None, None

    weekday_name = match.group(1)
    target_weekday = _WEEKDAY_NAMES[weekday_name]
    today_weekday = now.weekday()
    days_back = (today_weekday - target_weekday) % 7

    # "last <weekday>" always means the previous occurrence, even if it was today
    if days_back == 0 and re.search(r"\blast\s+" + re.escape(weekday_name), query):
        days_back = 7
    # bare weekday (no "this") when today IS that weekday → return last week's occurrence
    elif days_back == 0 and not re.search(r"\bthis\s+" + re.escape(weekday_name), query):
        days_back = 7

    target_date = (now - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
    return target_date, target_date + timedelta(days=1), f"weekday_{weekday_name}"


def _parse_named_date(query: str, now: datetime) -> tuple[datetime | None, datetime | None, str | None]:
    """Parse named-month dates: 'March 5th', '5 march', 'March 5 2025'."""
    # "march 5th" / "march 5" / "march 5th 2025" / "march 5 2025"
    fwd_match = _MONTH_PATTERN_FWD.search(query)
    if fwd_match:
        month_num = _MONTH_NAMES[fwd_match.group(1)]
        day_num = int(fwd_match.group(2))
        year = int(fwd_match.group(3)) if fwd_match.group(3) else now.year
        try:
            day = datetime(year, month_num, day_num, tzinfo=timezone.utc)
            if fwd_match.group(3) is None and day > now:
                day = datetime(year - 1, month_num, day_num, tzinfo=timezone.utc)
            return day, day + timedelta(days=1), "named_date"
        except ValueError:
            pass

    # "5th march" / "5 march" / "5th march 2025"
    rev_match = _MONTH_PATTERN_REV.search(query)
    if rev_match:
        day_num = int(rev_match.group(1))
        month_num = _MONTH_NAMES[rev_match.group(2)]
        year = int(rev_match.group(3)) if rev_match.group(3) else now.year
        try:
            day = datetime(year, month_num, day_num, tzinfo=timezone.utc)
            if rev_match.group(3) is None and day > now:
                day = datetime(year - 1, month_num, day_num, tzinfo=timezone.utc)
            return day, day + timedelta(days=1), "named_date"
        except ValueError:
            pass

    return None, None, None


def _parse_date_range(query: str) -> tuple[datetime | None, datetime | None, str | None]:
    now = datetime.now(timezone.utc)

    if re.search(r"\btoday\b", query):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1), "today"

    if re.search(r"\byesterday\b", query):
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
        return start, end, "yesterday"

    if re.search(r"\blast\s+weekend\b", query):
        today_weekday = now.weekday()
        days_to_last_saturday = (today_weekday - 5) % 7 or 7
        last_saturday = (now - timedelta(days=days_to_last_saturday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return last_saturday, last_saturday + timedelta(days=2), "last_weekend"

    if re.search(r"\bthis\s+weekend\b", query):
        today_weekday = now.weekday()
        days_to_saturday = (5 - today_weekday) % 7
        this_saturday = (now + timedelta(days=days_to_saturday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return this_saturday, this_saturday + timedelta(days=2), "this_weekend"

    if re.search(r"\blast\s+week\b", query):
        end = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=7)
        return start, end, "last_week"

    if re.search(r"\bthis\s+week\b", query):
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, now, "this_week"

    hours_match = re.search(r"\blast\s+(\d+)\s+hours?\b", query)
    if hours_match:
        hours = max(int(hours_match.group(1)), 1)
        return now - timedelta(hours=hours), now, f"last_{hours}_hours"

    days_match = re.search(r"\blast\s+(\d+)\s+days?\b", query)
    if days_match:
        days = max(int(days_match.group(1)), 1)
        return now - timedelta(days=days), now, f"last_{days}_days"

    # ISO date "2025-03-05"
    date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", query)
    if date_match:
        try:
            day = datetime.fromisoformat(date_match.group(1)).replace(tzinfo=timezone.utc)
        except ValueError:
            return None, None, None
        return day, day + timedelta(days=1), "explicit_date"

    # Named-month date ("March 5th", "5 march")
    named = _parse_named_date(query, now)
    if named[0] is not None:
        return named

    # Day-of-week ("last Monday", "on Wednesday")
    weekday = _parse_weekday_range(query, now)
    if weekday[0] is not None:
        return weekday

    return None, None, None


def _parse_camera_ids(query: str) -> list[str]:
    matches = re.findall(r"\b(?:camera|cam)\s+([a-z0-9][a-z0-9_\-]*)\b", query)
    return sorted({_normalize_camera(match) for match in matches if match.strip()})


def _parse_zones(query: str) -> list[str]:
    matches: list[str] = []
    stop_tokens = {"today", "yesterday", "recent", "latest", "last", "hours", "days", "camera"}
    for match in re.findall(r"\bzone\s+([a-z0-9][a-z0-9_\-]*)\b", query):
        normalized = _normalize_token(match)
        if normalized and normalized not in stop_tokens:
            matches.append(normalized)
    for match in re.findall(r"\b(?:in|at|near)\s+([a-z0-9][a-z0-9_\-]*(?:\s+[a-z0-9][a-z0-9_\-]*)?)\s+zone\b", query):
        normalized = _normalize_token(match)
        if normalized and not any(token in stop_tokens for token in normalized.split()):
            matches.append(normalized)
    if "entrance" in query:
        matches.append("entrance")
    if "exit" in query:
        matches.append("exit")
    if "parking" in query:
        matches.append("parking")
    if "lobby" in query:
        matches.append("lobby")
    if "loading dock" in query:
        matches.append("loading dock")
    if "dock" in query:
        matches.append("dock")
    gate_match = re.search(r"\bgate\s+([a-z0-9][a-z0-9_\-]*)\b", query)
    if gate_match:
        matches.append(f"gate {gate_match.group(1)}")
    return sorted(set(matches))


def _parse_object_classes(query: str) -> list[str]:
    matches: list[str] = []
    for canonical, aliases in OBJECT_CLASS_ALIASES.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", query) for alias in aliases):
            matches.append(canonical)
    return sorted(set(matches))


def _parse_event_types(query: str) -> list[str]:
    matches: list[str] = []
    for canonical, aliases in EVENT_TYPE_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", query) for alias in aliases):
            matches.append(canonical)
    return sorted(set(matches))


def _is_camera_inventory_query(query: str) -> bool:
    normalized = _normalize_token(query)
    if normalized in {"camera", "cameras"}:
        return True
    patterns = (
        r"\bwhich cameras\b",
        r"\blist cameras\b",
        r"\bshow cameras\b",
        r"\bavailable cameras\b",
        r"\bcamera list\b",
        r"\bcamera summary\b",
        r"\bcamera overview\b",
        r"\ball cameras\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_movement_summary_query(query: str) -> bool:
    normalized = _normalize_token(query)
    if normalized in {"movement", "movements", "total movement", "total movements"}:
        return True
    patterns = (
        r"\btotal movement\b",
        r"\bmovement total\b",
        r"\bmovement summary\b",
        r"\bmovement overview\b",
        r"\boverall movement\b",
        r"\bhow much movement\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_intrusion_summary_query(query: str) -> bool:
    normalized = _normalize_token(query)
    if normalized in {"intrusion", "intrusions", "total intrusion", "total intrusions"}:
        return True
    patterns = (
        r"\btotal intrusions?\b",
        r"\bintrusion summary\b",
        r"\bintrusion overview\b",
        r"\boverall intrusions?\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_follow_up_query(query: str) -> bool:
    normalized = _normalize_token(query)
    if normalized in {"details", "detail", "more", "more information"}:
        return True
    return any(phrase in normalized for phrase in FOLLOW_UP_PHRASES)


def _event_classes(event: dict[str, Any]) -> list[str]:
    classes_value = event.get("classes")
    if isinstance(classes_value, dict):
        normalized = [_normalize_object_class(str(k)) for k, v in classes_value.items() if str(k).strip() and v]
        if normalized:
            return normalized
    if isinstance(classes_value, list):
        normalized: list[str] = []
        for value in classes_value:
            if isinstance(value, str):
                normalized.append(_normalize_object_class(value))
        if normalized:
            return normalized

    class_label = event.get("class_label")
    if isinstance(class_label, str) and class_label.strip():
        return [_normalize_object_class(class_label)]

    return []


def _event_hour(event: dict[str, Any]) -> int | None:
    timestamp_value = event.get("timestamp")
    parsed = _parse_iso_datetime(timestamp_value)
    if parsed is not None:
        return parsed.hour

    fallback = re.search(r"\b(\d{2}):\d{2}(?::\d{2})?\b", str(timestamp_value or ""))
    if not fallback:
        return None

    try:
        hour = int(fallback.group(1))
    except ValueError:
        return None
    return hour if 0 <= hour <= 23 else None


def _event_date(event: dict[str, Any]) -> date | None:
    parsed = _parse_iso_datetime(event.get("timestamp"))
    if parsed is not None:
        return parsed.date()

    fallback = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", str(event.get("timestamp") or ""))
    if not fallback:
        return None

    try:
        return date(int(fallback.group(1)), int(fallback.group(2)), int(fallback.group(3)))
    except ValueError:
        return None


def _hour_in_range(hour: int, start_hour: int, end_hour: int) -> bool:
    if start_hour <= end_hour:
        return start_hour <= hour <= end_hour
    return hour >= start_hour or hour <= end_hour


def _matches_event_type(event: dict[str, Any], event_type: str) -> bool:
    if event_type == "intrusion":
        return bool(event.get("has_intrusion", event.get("intrusion", False)))
    if event_type == "movement":
        return bool(event.get("has_movement", True))
    return True


def parse_query(query: str) -> dict[str, Any]:
    normalized = _normalize_token(query or "")
    start_hour, end_hour = _parse_time_window(normalized)
    # Fall back to time-of-day label ("morning", "overnight", …) when no explicit time range
    if start_hour is None:
        start_hour, end_hour = _parse_time_of_day_label(normalized)
    date_from, date_to, date_scope = _parse_date_range(normalized)
    object_classes = _parse_object_classes(normalized)
    event_types = _parse_event_types(normalized)
    camera_ids = _parse_camera_ids(normalized)
    zones = _parse_zones(normalized)

    recency_intent = any(keyword in normalized for keyword in RECENCY_KEYWORDS)
    anomaly_intent = any(keyword in normalized for keyword in ANOMALY_KEYWORDS)
    pattern_intent = any(keyword in normalized for keyword in PATTERN_KEYWORDS) or "repeated intrusion" in normalized
    trend_intent = any(keyword in normalized for keyword in TREND_KEYWORDS)
    count_intent = bool(re.search(r"\b(how many|count|number of)\b", normalized))
    camera_inventory_intent = _is_camera_inventory_query(normalized)
    movement_summary_intent = _is_movement_summary_query(normalized)
    intrusion_summary_intent = _is_intrusion_summary_query(normalized)
    follow_up_intent = _is_follow_up_query(normalized)
    aggregate_intent = bool(
        count_intent
        or camera_inventory_intent
        or movement_summary_intent
        or intrusion_summary_intent
        or re.search(r"\b(summary|overview|breakdown|overall|total)\b", normalized)
    )

    has_hard_filters = any(
        (
            start_hour is not None and end_hour is not None,
            bool(object_classes),
            bool(event_types),
            bool(camera_ids),
            bool(zones),
            date_from is not None,
            date_to is not None,
        )
    )

    return {
        "normalized_query": normalized,
        "start_hour": start_hour,
        "end_hour": end_hour,
        "date_from": date_from,
        "date_to": date_to,
        "date_scope": date_scope,
        "camera_ids": camera_ids,
        "object_classes": object_classes,
        "event_types": event_types,
        "zones": zones,
        "recency_intent": recency_intent,
        "anomaly_intent": anomaly_intent,
        "pattern_intent": pattern_intent,
        "trend_intent": trend_intent,
        "count_intent": count_intent,
        "camera_inventory_intent": camera_inventory_intent,
        "movement_summary_intent": movement_summary_intent,
        "intrusion_summary_intent": intrusion_summary_intent,
        "follow_up_intent": follow_up_intent,
        "aggregate_intent": aggregate_intent,
        "has_hard_filters": has_hard_filters,
        "has_structured_filters": has_hard_filters or recency_intent or anomaly_intent or pattern_intent or trend_intent,
    }


def normalize_query(query: str) -> str:
    """Normalise time expressions (e.g. '3pm' → '3:00 pm') and collapse whitespace."""
    q = (query or "").lower().strip()

    def _replace_time(match: re.Match[str]) -> str:
        hour = int(match.group(1))
        minute = match.group(2)
        meridiem = match.group(3)
        if minute is None:
            return f"{hour}:00 {meridiem}"
        return f"{hour}:{minute} {meridiem}"

    q = re.sub(r"\b(\d{1,2})(?:\s*:\s*(\d{2}))?\s*(am|pm)\b", _replace_time, q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def serialize_filters(filters: dict[str, Any]) -> dict[str, Any]:
    serialized = dict(filters)
    for key in ("date_from", "date_to"):
        value = serialized.get(key)
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
    return serialized


def _document_timestamp_range(document: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return None, None
    start = _parse_iso_datetime(metadata.get("window_start") or metadata.get("timestamp"))
    end = _parse_iso_datetime(metadata.get("window_end") or metadata.get("timestamp"))
    return start, end


def _document_values(document: dict[str, Any], field: str) -> list[str]:
    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        return []

    value = metadata.get(field)
    if isinstance(value, list):
        return [_normalize_token(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [_normalize_token(item) for item in value.split(",") if item.strip()]
    return []


def filter_documents(documents: list[dict[str, Any]], filters: dict[str, Any]) -> list[int]:
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    start_hour = filters.get("start_hour")
    end_hour = filters.get("end_hour")
    camera_ids = {_normalize_camera(value) for value in filters.get("camera_ids", [])}
    object_classes = {_normalize_object_class(value) for value in filters.get("object_classes", [])}
    event_types = {_normalize_token(value) for value in filters.get("event_types", [])}
    zones = {_normalize_token(value) for value in filters.get("zones", [])}
    pattern_intent = bool(filters.get("pattern_intent"))

    candidate_indices: list[int] = []
    for index, document in enumerate(documents):
        metadata = document.get("metadata")
        if not isinstance(metadata, dict):
            continue

        doc_camera = _normalize_camera(str(metadata.get("camera_id", "")))
        if not _camera_matches(doc_camera, camera_ids):
            continue

        doc_event_types = set(_document_values(document, "event_types") or _document_values(document, "event_type"))
        if event_types and not doc_event_types.intersection(event_types):
            continue

        doc_classes = set(_document_values(document, "classes") or _document_values(document, "class_label"))
        if object_classes and not doc_classes.intersection(object_classes):
            continue

        doc_zones = set(_document_values(document, "zones") or _document_values(document, "zone"))
        if zones and not any(any(zone in doc_zone for doc_zone in doc_zones) for zone in zones):
            continue

        window_start, window_end = _document_timestamp_range(document)
        if date_from is not None:
            if window_end is None or window_end < date_from:
                continue
        if date_to is not None:
            if window_start is None or window_start > date_to:
                continue

        if start_hour is not None and end_hour is not None:
            timestamp = window_end or window_start
            if timestamp is None or not _hour_in_range(timestamp.hour, int(start_hour), int(end_hour)):
                continue

        if pattern_intent and int(metadata.get("event_count", 1)) < 2:
            continue

        candidate_indices.append(index)

    return candidate_indices


def filter_events(events: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    start_hour = filters.get("start_hour")
    end_hour = filters.get("end_hour")
    object_classes = {_normalize_object_class(value) for value in filters.get("object_classes", [])}
    event_types = {_normalize_token(value) for value in filters.get("event_types", [])}
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    camera_ids = {_normalize_camera(value) for value in filters.get("camera_ids", [])}
    zones = {_normalize_token(value) for value in filters.get("zones", [])}

    filtered: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue

        event_camera = _normalize_camera(str(event.get("camera_id", "")))
        if not _camera_matches(event_camera, camera_ids):
            continue

        if date_from is not None or date_to is not None:
            timestamp = _parse_iso_datetime(event.get("timestamp"))
            if timestamp is None:
                continue
            if date_from is not None and timestamp < date_from:
                continue
            if date_to is not None and timestamp > date_to:
                continue

        if start_hour is not None and end_hour is not None:
            hour = _event_hour(event)
            if hour is None or not _hour_in_range(hour, int(start_hour), int(end_hour)):
                continue

        if object_classes:
            classes = set(_event_classes(event))
            if not classes.intersection(object_classes):
                continue

        if event_types and not any(_matches_event_type(event, event_type) for event_type in event_types):
            continue

        event_zones = {
            _normalize_token(zone)
            for zone in (event.get("roi_zone_names") or event.get("roi_zone_ids") or [])
            if str(zone).strip()
        }
        if zones and not any(any(zone in event_zone for event_zone in event_zones) for zone in zones):
            continue

        filtered.append(event)

    return filtered


def summarize_events(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        if not isinstance(event, dict):
            continue

        classes = _event_classes(event)
        if not classes:
            continue

        for class_name in classes:
            counts[class_name] = counts.get(class_name, 0) + 1

    return counts
