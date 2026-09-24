"""Cross-service events: typed contracts, transactional outbox, idempotent inbox.

A producer writes the event into its *own* database in the same transaction
as the state change that caused it (``enqueue``); a background dispatcher
delivers pending rows to the peer's ``POST /internal/v1/events`` over mTLS
with retries and backoff. The consumer records each event id in an inbox
table before handling it, so redelivery is harmless. No broker is shared
between machines, and no event is lost when a peer is down.
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping, Optional, Type

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Column, DateTime, Integer, JSON, String, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

EVENTS_PATH = "/internal/v1/events"
ENVELOPE_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# -- contracts -----------------------------------------------------------------


class _EventData(BaseModel):
    model_config = ConfigDict(extra="forbid")


PlateText = Field(min_length=2, max_length=20, pattern=r"^[A-Z0-9]+$")


class VehicleGatePassed(_EventData):
    """A plate was read at a gate (parking -> surveillance)."""

    plate: str = PlateText
    direction: Literal["entry", "exit"]
    camera_id: str = Field(max_length=64)
    profile_type: Optional[str] = Field(default=None, max_length=20)
    authorized: bool = False
    threat: bool = False
    decision: Literal["allow", "deny", "verify", "unknown"] = "unknown"
    identity_id: Optional[str] = Field(default=None, max_length=64)
    identity_label: Optional[str] = Field(default=None, max_length=120)


class ParkingAlertRaised(_EventData):
    """A parking-side alert to surface on the security console."""

    alert_id: str = Field(max_length=64)
    alert_type: str = Field(max_length=100)
    severity: Literal["low", "medium", "high", "critical"]
    camera_id: Optional[str] = Field(default=None, max_length=64)
    description: str = Field(max_length=2000)
    space_id: Optional[str] = Field(default=None, max_length=50)
    plate: Optional[str] = Field(default=None, max_length=20)


class VehicleTheftSuspected(_EventData):
    """A vehicle left with an unverified or unauthorized driver."""

    plate: str = PlateText
    camera_id: Optional[str] = Field(default=None, max_length=64)
    space_id: Optional[str] = Field(default=None, max_length=50)
    reason: str = Field(max_length=500)
    identity_id: Optional[str] = Field(default=None, max_length=64)


class ArmModeChanged(_EventData):
    """Site arming changed (surveillance -> parking)."""

    mode: Literal["armed", "disarmed", "auto"]
    actor: Optional[str] = Field(default=None, max_length=255)


EVENT_SCHEMAS: dict[str, Type[_EventData]] = {
    "vehicle.gate_passed": VehicleGatePassed,
    "parking.alert": ParkingAlertRaised,
    "vehicle.theft_suspected": VehicleTheftSuspected,
    "security.arm_mode_changed": ArmModeChanged,
}


class EventError(ValueError):
    """Envelope or payload does not match the contract."""


@dataclass(frozen=True)
class EventEnvelope:
    id: str
    type: str
    source: str
    tenant_id: str
    occurred_at: str
    data: dict[str, Any]
    version: int = ENVELOPE_VERSION

    @classmethod
    def create(
        cls, event_type: str, source: str, tenant_id: str, data: _EventData | Mapping
    ) -> "EventEnvelope":
        payload = data.model_dump() if isinstance(data, BaseModel) else dict(data)
        envelope = cls(
            id=uuid.uuid4().hex,
            type=event_type,
            source=source,
            tenant_id=str(tenant_id),
            occurred_at=datetime.now(timezone.utc).isoformat(),
            data=payload,
        )
        envelope.validated_data()
        return envelope

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "tenant_id": self.tenant_id,
            "occurred_at": self.occurred_at,
            "data": self.data,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "EventEnvelope":
        if not isinstance(raw, dict):
            raise EventError("envelope must be an object")
        try:
            envelope = cls(
                id=str(raw["id"]),
                type=str(raw["type"]),
                source=str(raw["source"]),
                tenant_id=str(raw["tenant_id"]),
                occurred_at=str(raw["occurred_at"]),
                data=dict(raw["data"]),
                version=int(raw.get("version", ENVELOPE_VERSION)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EventError("malformed envelope") from exc
        if envelope.version != ENVELOPE_VERSION:
            raise EventError(f"unsupported envelope version {envelope.version}")
        if not (0 < len(envelope.id) <= 64 and 0 < len(envelope.tenant_id) <= 36):
            raise EventError("invalid id or tenant")
        envelope.validated_data()
        return envelope

    def validated_data(self) -> _EventData:
        schema = EVENT_SCHEMAS.get(self.type)
        if schema is None:
            raise EventError(f"unknown event type {self.type!r}")
        try:
            return schema.model_validate(self.data)
        except ValidationError as exc:
            raise EventError(f"invalid {self.type} payload: {exc.error_count()} error(s)") from exc


# -- persistence mixins ----------------------------------------------------------


class OutboxEventMixin:
    """One row per (event, destination). Mix into the service's Base."""

    __tablename__ = "outbox_events"

    id = Column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)
    event_id = Column(String(64), nullable=False, index=True)
    event_type = Column(String(100), nullable=False)
    tenant_id = Column(String(36), nullable=False, index=True)
    destination = Column(String(64), nullable=False)
    envelope = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=_utcnow, index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    delivered_at = Column(DateTime, nullable=True)


class InboxEventMixin:
    """Event ids already processed (idempotency)."""

    __tablename__ = "inbox_events"

    event_id = Column(String(64), primary_key=True)
    source = Column(String(64), nullable=False)
    event_type = Column(String(100), nullable=False)
    tenant_id = Column(String(36), nullable=False)
    received_at = Column(DateTime, nullable=False, default=_utcnow)


def enqueue(
    session: AsyncSession | Any,
    outbox_model: Type[OutboxEventMixin],
    envelope: EventEnvelope,
    destinations: list[str],
) -> None:
    """Stage ``envelope`` for delivery inside the caller's transaction."""
    for destination in destinations:
        session.add(
            outbox_model(
                event_id=envelope.id,
                event_type=envelope.type,
                tenant_id=envelope.tenant_id,
                destination=destination,
                envelope=envelope.to_dict(),
            )
        )


async def record_inbox(
    session: AsyncSession, inbox_model: Type[InboxEventMixin], envelope: EventEnvelope
) -> bool:
    """Record ``envelope`` as received; False if it was already processed."""
    existing = await session.get(inbox_model, envelope.id)
    if existing is not None:
        return False
    session.add(
        inbox_model(
            event_id=envelope.id,
            source=envelope.source,
            event_type=envelope.type,
            tenant_id=envelope.tenant_id,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        return False
    return True


# -- dispatcher ------------------------------------------------------------------


def backoff_seconds(attempts: int) -> float:
    base = min(300.0, 2.0 ** min(attempts, 9))
    return base * (0.8 + 0.4 * random.random())


PERMANENT_STATUSES = {400, 404, 405, 413, 415, 422}


class OutboxDispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        outbox_model: Type[OutboxEventMixin],
        clients: Mapping[str, httpx.AsyncClient],
        *,
        batch_size: int = 50,
        max_attempts: int = 12,
        poll_interval: float = 1.0,
    ) -> None:
        self._session_factory = session_factory
        self._model = outbox_model
        self._clients = dict(clients)
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._poll_interval = poll_interval
        self.delivered = 0
        self.failed = 0
        self.dead = 0

    async def run_once(self) -> int:
        model = self._model
        now = _utcnow()
        async with self._session_factory() as session:
            query = (
                select(model)
                .where(model.status == "pending", model.next_attempt_at <= now)
                .order_by(model.created_at)
                .limit(self._batch_size)
            )
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            rows = (await session.execute(query)).scalars().all()
            delivered = 0
            for row in rows:
                if await self._deliver(row):
                    delivered += 1
            await session.commit()
        return delivered

    async def _deliver(self, row: OutboxEventMixin) -> bool:
        client = self._clients.get(row.destination)
        row.attempts = int(row.attempts or 0) + 1
        if client is None:
            return self._fail(row, f"no client configured for {row.destination!r}", permanent=False)
        try:
            response = await client.post(EVENTS_PATH, json=row.envelope)
        except httpx.HTTPError as exc:
            return self._fail(row, f"{type(exc).__name__}: {exc}", permanent=False)
        if 200 <= response.status_code < 300 or response.status_code == 409:
            row.status = "delivered"
            row.delivered_at = _utcnow()
            row.last_error = None
            self.delivered += 1
            return True
        permanent = response.status_code in PERMANENT_STATUSES
        return self._fail(row, f"HTTP {response.status_code}", permanent=permanent)

    def _fail(self, row: OutboxEventMixin, error: str, *, permanent: bool) -> bool:
        row.last_error = error[:500]
        self.failed += 1
        if permanent or row.attempts >= self._max_attempts:
            row.status = "dead"
            self.dead += 1
            logger.error(
                "Outbox event %s (%s) to %s dead-lettered after %s attempt(s): %s",
                row.event_id, row.event_type, row.destination, row.attempts, error,
            )
        else:
            row.next_attempt_at = _utcnow() + timedelta(seconds=backoff_seconds(row.attempts))
            logger.warning(
                "Outbox event %s to %s failed (attempt %s): %s",
                row.event_id, row.destination, row.attempts, error,
            )
        return False

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                delivered = await self.run_once()
            except Exception:  # never let the loop die on one bad batch
                logger.exception("Outbox dispatch cycle failed")
                delivered = 0
            if delivered == 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._poll_interval)
                except asyncio.TimeoutError:
                    pass

    def stats(self) -> dict[str, int]:
        return {"delivered": self.delivered, "failed": self.failed, "dead": self.dead}
