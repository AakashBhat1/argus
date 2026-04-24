"""Shared utilities for the backend application."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return current UTC time as a naive datetime.

    Replaces the deprecated ``datetime.utcnow()`` (removed in Python 3.12+)
    while keeping the return value naive so it stays compatible with existing
    SQLAlchemy ``DateTime`` columns that store UTC without timezone info.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
