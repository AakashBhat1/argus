from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from argus_common.audit import (
    REDACTED,
    AuditActor,
    AuditEventMixin,
    AuditQueryError,
    add_audit_event,
    decode_cursor,
    list_audit_events,
    purge_audit_events,
    sanitize_details,
)

Base = declarative_base()


class AuditEvent(AuditEventMixin, Base):
    pass


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


ADMIN_A = AuditActor(name="alice", tenant_id="A", id="u1")
ADMIN_B = AuditActor(name="bob", tenant_id="B", id="u2")


def test_details_never_keep_credentials():
    clean = sanitize_details(
        {
            "password": "S3cret!",
            "nested": {"api_key": "k", "refresh_token": "t", "ok": 1},
            "stream_url": "rtsp://admin:S3cret@10.0.0.5/live?password=S3cret",
            "list": ["rtsp://u:S3cret@cam/1"],
        }
    )
    assert "S3cret" not in repr(clean)
    assert clean["password"] == REDACTED
    assert clean["nested"] == {"api_key": REDACTED, "refresh_token": REDACTED, "ok": 1}
    assert clean["stream_url"].startswith("rtsp://***@10.0.0.5/live")


def test_details_are_bounded():
    clean = sanitize_details({"long": "x" * 5000, "many": list(range(200)), "deep": {"a": {"b": {"c": {"d": {"e": 1}}}}}})
    assert len(clean["long"]) <= 501
    assert len(clean["many"]) == 51
    assert clean["deep"]["a"]["b"]["c"]["d"] == "[truncated]"


async def test_row_is_staged_not_committed(session_factory):
    async with session_factory() as db:
        add_audit_event(db, AuditEvent, actor=ADMIN_A, action="camera.create", target_type="camera", target_id="c1")
        await db.rollback()
    async with session_factory() as db:
        assert (await db.execute(select(func.count()).select_from(AuditEvent))).scalar() == 0


async def test_action_must_be_dotted(session_factory):
    async with session_factory() as db:
        with pytest.raises(ValueError):
            add_audit_event(db, AuditEvent, actor=ADMIN_A, action="Camera Create")


async def test_listing_is_tenant_scoped_filtered_and_paginated(session_factory):
    base = datetime(2026, 1, 1)
    async with session_factory() as db:
        for i in range(5):
            row = add_audit_event(db, AuditEvent, actor=ADMIN_A, action="camera.update", target_id=f"c{i}")
            row.created_at = base + timedelta(minutes=i)
        add_audit_event(db, AuditEvent, actor=ADMIN_A, action="zone.create").created_at = base
        add_audit_event(db, AuditEvent, actor=ADMIN_B, action="camera.update").created_at = base
        await db.commit()

    async with session_factory() as db:
        first = await list_audit_events(db, AuditEvent, tenant_id="A", action="camera.*", limit=2)
        assert [e.target_id for e in first.items] == ["c4", "c3"]
        assert first.next_cursor
        second = await list_audit_events(db, AuditEvent, tenant_id="A", action="camera.*", limit=2, cursor=first.next_cursor)
        third = await list_audit_events(db, AuditEvent, tenant_id="A", action="camera.*", limit=2, cursor=second.next_cursor)
        assert [e.target_id for e in second.items + third.items] == ["c2", "c1", "c0"]
        assert third.next_cursor is None

        only_b = await list_audit_events(db, AuditEvent, tenant_id="B")
        assert [e.actor for e in only_b.items] == ["bob"]

        window = await list_audit_events(
            db, AuditEvent, tenant_id="A", since=base + timedelta(minutes=1), until=base + timedelta(minutes=3)
        )
        assert [e.target_id for e in window.items] == ["c2", "c1"]
        assert (await list_audit_events(db, AuditEvent, tenant_id="A", actor="nobody")).items == []


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 501}, {"action": "drop table;"}, {"cursor": "%%%"}])
async def test_bad_queries_are_rejected(session_factory, kwargs):
    async with session_factory() as db:
        with pytest.raises(AuditQueryError):
            await list_audit_events(db, AuditEvent, tenant_id="A", **kwargs)


def test_cursor_round_trip():
    from argus_common.audit import encode_cursor

    stamp = datetime(2026, 5, 4, 3, 2, 1, 123456)
    assert decode_cursor(encode_cursor(stamp, "abc")) == (stamp, "abc")


async def test_retention_purges_only_old_rows(session_factory):
    now = datetime(2026, 6, 1)
    async with session_factory() as db:
        add_audit_event(db, AuditEvent, actor=ADMIN_A, action="auth.login.success").created_at = now - timedelta(days=400)
        add_audit_event(db, AuditEvent, actor=ADMIN_A, action="auth.login.success").created_at = now - timedelta(days=10)
        await db.commit()
        assert await purge_audit_events(db, AuditEvent, retention_days=365, now=now) == 1
        await db.commit()
        assert (await db.execute(select(func.count()).select_from(AuditEvent))).scalar() == 1
    with pytest.raises(ValueError):
        async with session_factory() as db:
            await purge_audit_events(db, AuditEvent, retention_days=0)
