"""The database-backed login limiter: one shared count for every worker."""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.models import LoginFailure, LoginLockout
from app.services.login_attempts import DatabaseLoginLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def fresh_tables(db_session):
    await db_session.execute(delete(LoginFailure))
    await db_session.execute(delete(LoginLockout))
    await db_session.commit()
    yield


def _limiter(clock, **overrides):
    options = dict(
        username_failure_limit=3,
        username_window_seconds=60.0,
        base_lockout_seconds=30.0,
        max_lockout_seconds=120.0,
        ip_failure_limit=5,
        ip_window_seconds=60.0,
        clock=clock,
        secret=lambda: b"test-secret",
    )
    options.update(overrides)
    return DatabaseLoginLimiter(**options)


async def _attempt(limiter, factory, username, ok=False, ip="203.0.113.10"):
    async with factory() as session:
        return await limiter.decide(session, source_ip=ip, username=username, credentials_valid=ok)


async def test_workers_share_one_lockout(fresh_tables, test_session_factory):
    clock = Clock()
    worker_a, worker_b = _limiter(clock), _limiter(clock)
    reasons = [
        (await _attempt(worker, test_session_factory, "Ops")).reason
        for worker in (worker_a, worker_b, worker_a)
    ]
    assert reasons == ["invalid_credentials", "invalid_credentials", "username_lockout"]
    # Locked on every worker, whatever the case of the username, even with the right password.
    assert (await _attempt(worker_b, test_session_factory, "ops", ok=True)).reason == "username_lockout"
    clock.now += 31
    assert (await _attempt(worker_b, test_session_factory, "ops", ok=True)).allowed


async def test_lockouts_grow_then_reset_after_a_quiet_window(fresh_tables, test_session_factory):
    clock = Clock()
    limiter = _limiter(clock, username_failure_limit=1)
    assert (await _attempt(limiter, test_session_factory, "x")).reason == "username_lockout"
    clock.now += 31
    assert (await _attempt(limiter, test_session_factory, "x")).reason == "username_lockout"
    clock.now += 31  # second lockout is 60 s
    assert (await _attempt(limiter, test_session_factory, "x", ok=True)).allowed is False
    clock.now += 30
    assert (await _attempt(limiter, test_session_factory, "x", ok=True)).allowed
    async with test_session_factory() as session:
        assert (await session.scalars(select(LoginLockout))).all() == []


async def test_ip_limit_blocks_spraying_but_not_other_addresses(fresh_tables, test_session_factory):
    clock = Clock()
    limiter = _limiter(clock, username_failure_limit=100)
    reasons = [(await _attempt(limiter, test_session_factory, f"guess-{i}")).reason for i in range(5)]
    assert reasons[-1] == "ip_rate_limit"
    assert (await _attempt(limiter, test_session_factory, "real", ok=True)).reason == "ip_rate_limit"
    assert (await _attempt(limiter, test_session_factory, "real", ok=True, ip="203.0.113.11")).allowed
    clock.now += 61
    assert (await _attempt(limiter, test_session_factory, "real", ok=True)).allowed


async def test_usernames_and_addresses_are_not_stored(fresh_tables, test_session_factory):
    clock = Clock()
    await _attempt(_limiter(clock), test_session_factory, "alice@example.com", ip="198.51.100.77")
    async with test_session_factory() as session:
        subjects = [row.subject for row in (await session.scalars(select(LoginFailure))).all()]
        subjects += [row.subject for row in (await session.scalars(select(LoginLockout))).all()]
    assert subjects and all("alice" not in s and "198.51" not in s and len(s) == 64 for s in subjects)


async def test_failures_survive_the_failed_request(fresh_tables, anon_client, db_session, monkeypatch):
    """The login route answers 401 (its transaction rolls back); the
    recorded failure must still count."""
    from app.routers import auth as auth_router

    clock = Clock()
    monkeypatch.setattr(auth_router, "login_attempt_limiter", _limiter(clock))
    for _ in range(3):
        response = await anon_client.post("/api/v1/auth/token", data={"username": "nobody", "password": "x"})
        assert response.status_code == 401
    lockouts = (await db_session.scalars(select(LoginLockout))).all()
    assert len(lockouts) == 1 and lockouts[0].locked_until > clock.now
