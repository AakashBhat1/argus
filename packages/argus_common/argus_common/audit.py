"""Admin audit log: who changed what, written in the same transaction as the change.

Each service mixes ``AuditEventMixin`` into its own declarative Base and calls
``add_audit_event`` *before* it commits the change being described. The row
is only ``add``-ed, never committed here, so a rolled-back change leaves no
audit row and an audit row never exists for a change that did not happen.

``details`` is sanitised on the way in: keys that name credentials are
masked, URLs lose their userinfo and secret parameters (``redact_url``),
and strings, lists and nesting are bounded. Callers must still never pass
secrets on purpose; the sanitiser is a backstop.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Optional, Sequence, Type

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, Column, DateTime, Index, String, and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from argus_common.net import redact_url

MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 100
REDACTED = "[redacted]"
_MAX_STRING = 500
_MAX_ITEMS = 50
_MAX_DEPTH = 4
_SECRET_KEY = re.compile(
    r"(?i)(pass(word|wd)?|pwd|secret|token|api[-_]?key|private[-_]?key|credential|authorization|cookie)"
)
_ACTION = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_ACTION_FILTER = re.compile(r"^[a-z][a-z0-9_.]*(\.\*)?$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuditEventMixin:
    """One audited action. Mix into the service's Base."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_tenant_created", "tenant_id", "created_at", "id"),)

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    tenant_id = Column(String(36), nullable=False)
    actor = Column(String(160), nullable=False)
    actor_id = Column(String(64), nullable=True)
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(40), nullable=True)
    target_id = Column(String(64), nullable=True)
    details = Column(JSON, nullable=True)
    source_ip = Column(String(64), nullable=True)


@dataclass(frozen=True)
class AuditActor:
    """Who performed an action: a user, a peer service or the system."""

    name: str
    tenant_id: str
    id: Optional[str] = None

    @classmethod
    def user(cls, user: Any) -> "AuditActor":
        return cls(name=str(user.username)[:160], tenant_id=str(user.tenant_id), id=_opt_str(getattr(user, "id", None)))

    @classmethod
    def service(cls, service: str, tenant_id: str) -> "AuditActor":
        return cls(name=f"service:{service}"[:160], tenant_id=str(tenant_id))

    @classmethod
    def system(cls, tenant_id: str, name: str = "system") -> "AuditActor":
        return cls(name=name[:160], tenant_id=str(tenant_id))


def _opt_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)[:64]


def sanitize_details(value: Any, _depth: int = 0) -> Any:
    """Bound and scrub a details payload so it is safe to store and show."""
    if _depth > _MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_ITEMS:
                clean["…"] = f"{len(value) - _MAX_ITEMS} more keys"
                break
            name = str(key)[:64]
            clean[name] = REDACTED if _SECRET_KEY.search(name) else sanitize_details(item, _depth + 1)
        return clean
    if isinstance(value, (list, tuple, set)):
        items = [sanitize_details(item, _depth + 1) for item in list(value)[:_MAX_ITEMS]]
        if len(value) > _MAX_ITEMS:
            items.append(f"… {len(value) - _MAX_ITEMS} more")
        return items
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    if "://" in text:
        text = redact_url(text)
    return text if len(text) <= _MAX_STRING else text[:_MAX_STRING] + "…"


def add_audit_event(
    db: AsyncSession,
    model: Type[AuditEventMixin],
    *,
    actor: AuditActor,
    action: str,
    target_type: Optional[str] = None,
    target_id: Any = None,
    details: Optional[Mapping[str, Any]] = None,
    source_ip: Optional[str] = None,
) -> AuditEventMixin:
    """Stage an audit row in ``db``'s transaction. The caller commits."""
    if not _ACTION.match(action) or len(action) > 64:
        raise ValueError(f"audit action must be dotted lowercase, got {action!r}")
    row = model(
        tenant_id=actor.tenant_id,
        actor=actor.name,
        actor_id=actor.id,
        action=action,
        target_type=target_type[:40] if target_type else None,
        target_id=_opt_str(target_id),
        details=sanitize_details(dict(details)) if details else None,
        source_ip=source_ip[:64] if source_ip else None,
    )
    db.add(row)
    return row


def source_ip_of(request: Any) -> Optional[str]:
    """The client address as uvicorn resolved it (forwarded headers only from the edge)."""
    client = getattr(request, "client", None) if request is not None else None
    return client.host if client else None


def make_recorder(model: Type[AuditEventMixin]) -> Callable[..., AuditEventMixin]:
    """``record(db, request, actor, action, target_type, target_id, details)`` bound to ``model``.

    ``actor`` is an AuditActor or any user object with username/tenant_id/id.
    """

    def record(
        db: AsyncSession,
        request: Any,
        actor: Any,
        action: str,
        target_type: Optional[str] = None,
        target_id: Any = None,
        details: Optional[Mapping[str, Any]] = None,
    ) -> AuditEventMixin:
        who = actor if isinstance(actor, AuditActor) else AuditActor.user(actor)
        return add_audit_event(
            db, model, actor=who, action=action, target_type=target_type,
            target_id=target_id, details=details, source_ip=source_ip_of(request),
        )

    return record


# -- reading -------------------------------------------------------------------


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    tenant_id: str
    actor: str
    actor_id: Optional[str] = None
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    source_ip: Optional[str] = None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    next_cursor: Optional[str] = None


class AuditQueryError(ValueError):
    """A filter or cursor the API cannot accept."""


def encode_cursor(created_at: datetime, row_id: str) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, _, row_id = base64.urlsafe_b64decode(padded.encode()).decode().partition("|")
        if not row_id or len(row_id) > 36:
            raise ValueError("missing id")
        return datetime.fromisoformat(stamp), row_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AuditQueryError("invalid cursor") from exc


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def list_audit_events(
    db: AsyncSession,
    model: Type[AuditEventMixin],
    *,
    tenant_id: str,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: Optional[str] = None,
) -> AuditPage:
    """Newest first, scoped to one tenant, keyset-paginated on (created_at, id).

    ``action`` matches exactly, or by prefix when it ends in ``.*``
    (``camera.*`` matches ``camera.create`` and ``camera.delete``).
    """
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise AuditQueryError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    conditions: list[Any] = [model.tenant_id == str(tenant_id)]
    if action:
        if not _ACTION_FILTER.match(action) or len(action) > 64:
            raise AuditQueryError("invalid action filter")
        if action.endswith(".*"):
            conditions.append(model.action.startswith(action[:-1], autoescape=True))
        else:
            conditions.append(model.action == action)
    if actor:
        conditions.append(model.actor == actor[:160])
    since, until = _naive_utc(since), _naive_utc(until)
    if since:
        conditions.append(model.created_at >= since)
    if until:
        conditions.append(model.created_at < until)
    if cursor:
        stamp, row_id = decode_cursor(cursor)
        conditions.append(
            or_(model.created_at < stamp, and_(model.created_at == stamp, model.id < row_id))
        )
    rows: Sequence[Any] = (
        await db.execute(
            select(model)
            .where(*conditions)
            .order_by(model.created_at.desc(), model.id.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    page = list(rows[:limit])
    next_cursor = encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
    return AuditPage(items=[AuditEventOut.model_validate(row) for row in page], next_cursor=next_cursor)


async def purge_audit_events(
    db: AsyncSession, model: Type[AuditEventMixin], *, retention_days: int, now: Optional[datetime] = None
) -> int:
    """Delete rows older than the retention window. The caller commits."""
    if retention_days < 1:
        raise ValueError("audit retention must be at least one day")
    cutoff = (now or _utcnow()) - timedelta(days=retention_days)
    result = await db.execute(delete(model).where(model.created_at < cutoff))
    return int(result.rowcount or 0)
