"""Outbox delivery, retries, dead-lettering and idempotent inbox."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from argus_common.events import (
    EventEnvelope,
    EventError,
    InboxEventMixin,
    OutboxDispatcher,
    OutboxEventMixin,
    VehicleGatePassed,
    enqueue,
    record_inbox,
)


class Base(DeclarativeBase):
    pass


class Outbox(OutboxEventMixin, Base):
    pass


class Inbox(InboxEventMixin, Base):
    pass


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _envelope(plate="KA01AB1234"):
    return EventEnvelope.create(
        "vehicle.gate_passed",
        "parking",
        "tenant-1",
        VehicleGatePassed(plate=plate, direction="entry", camera_id="gate-1", authorized=True),
    )


def _client(handler):
    return httpx.AsyncClient(base_url="http://peer", transport=httpx.MockTransport(handler))


def test_contract_rejects_unknown_types_and_bad_payloads():
    with pytest.raises(EventError, match="unknown event type"):
        EventEnvelope.create("vehicle.teleported", "parking", "t", {})
    with pytest.raises(EventError):
        EventEnvelope.create("vehicle.gate_passed", "parking", "t", {"plate": "ka 01; DROP", "direction": "entry", "camera_id": "c"})
    raw = _envelope().to_dict()
    raw["data"]["unexpected"] = "field"
    with pytest.raises(EventError):
        EventEnvelope.from_dict(raw)


async def test_outbox_delivers_and_marks_rows(session_factory):
    received = []

    def handler(request):
        received.append(request)
        return httpx.Response(202)

    async with session_factory() as session:
        enqueue(session, Outbox, _envelope(), ["surveillance"])
        await session.commit()

    dispatcher = OutboxDispatcher(session_factory, Outbox, {"surveillance": _client(handler)})
    assert await dispatcher.run_once() == 1
    assert received[0].url.path == "/internal/v1/events"
    async with session_factory() as session:
        row = (await session.execute(select(Outbox))).scalar_one()
        assert row.status == "delivered" and row.delivered_at is not None
    assert await dispatcher.run_once() == 0


async def test_transient_failure_is_retried_with_backoff(session_factory):
    responses = iter([httpx.Response(503), httpx.Response(200)])

    async with session_factory() as session:
        enqueue(session, Outbox, _envelope(), ["surveillance"])
        await session.commit()

    dispatcher = OutboxDispatcher(session_factory, Outbox, {"surveillance": _client(lambda r: next(responses))})
    assert await dispatcher.run_once() == 0
    async with session_factory() as session:
        row = (await session.execute(select(Outbox))).scalar_one()
        assert row.status == "pending" and row.attempts == 1 and "503" in row.last_error
        row.next_attempt_at = row.next_attempt_at - timedelta(hours=1)
        await session.commit()
    assert await dispatcher.run_once() == 1


async def test_network_errors_do_not_lose_events(session_factory):
    def handler(request):
        raise httpx.ConnectError("peer down")

    async with session_factory() as session:
        enqueue(session, Outbox, _envelope(), ["surveillance"])
        await session.commit()
    dispatcher = OutboxDispatcher(session_factory, Outbox, {"surveillance": _client(handler)})
    await dispatcher.run_once()
    async with session_factory() as session:
        row = (await session.execute(select(Outbox))).scalar_one()
        assert row.status == "pending" and "ConnectError" in row.last_error


async def test_permanent_rejection_and_max_attempts_dead_letter(session_factory):
    async with session_factory() as session:
        enqueue(session, Outbox, _envelope(), ["surveillance", "face"])
        await session.commit()
    dispatcher = OutboxDispatcher(
        session_factory,
        Outbox,
        {"surveillance": _client(lambda r: httpx.Response(422)), "face": _client(lambda r: httpx.Response(500))},
        max_attempts=1,
    )
    await dispatcher.run_once()
    async with session_factory() as session:
        statuses = {row.destination: row.status for row in (await session.execute(select(Outbox))).scalars()}
    assert statuses == {"surveillance": "dead", "face": "dead"}
    assert dispatcher.stats()["dead"] == 2


async def test_duplicate_delivery_is_treated_as_success(session_factory):
    async with session_factory() as session:
        enqueue(session, Outbox, _envelope(), ["surveillance"])
        await session.commit()
    dispatcher = OutboxDispatcher(session_factory, Outbox, {"surveillance": _client(lambda r: httpx.Response(409))})
    assert await dispatcher.run_once() == 1


async def test_inbox_is_idempotent(session_factory):
    envelope = _envelope()
    async with session_factory() as session:
        assert await record_inbox(session, Inbox, envelope) is True
        await session.commit()
    async with session_factory() as session:
        assert await record_inbox(session, Inbox, envelope) is False


def test_envelope_round_trip():
    envelope = _envelope()
    again = EventEnvelope.from_dict(envelope.to_dict())
    assert again == envelope
    assert isinstance(again.validated_data(), VehicleGatePassed)
