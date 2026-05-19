"""Shared utilities for the backend application."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return current UTC time as a naive datetime.

    Replaces the deprecated ``datetime.utcnow()`` (removed in Python 3.12+)
    while keeping the return value naive so it stays compatible with existing
    SQLAlchemy ``DateTime`` columns that store UTC without timezone info.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(v: datetime) -> str:
    """Serialize a datetime as ISO 8601 UTC with a trailing ``Z``.

    Naive datetimes are assumed to be UTC, matching what :func:`utc_now`
    produces. The frontend's ``new Date(...)`` only treats the string as UTC
    when a timezone designator is present, so we must always emit one.
    """
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
