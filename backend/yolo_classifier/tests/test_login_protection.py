"""Security regressions for login throttling, lockout, and timing parity."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserRole
from app.routers import auth as auth_router
from app.services.auth import get_password_hash
from app.services.login_attempts import LoginAttemptLimiter


@dataclass
class FakeClock:
    now: float = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def login_guard(monkeypatch):
    clock = FakeClock()
    limiter = LoginAttemptLimiter(
        username_failure_limit=3,
        username_window_seconds=60.0,
        base_lockout_seconds=30.0,
        max_lockout_seconds=120.0,
        ip_failure_limit=10,
        ip_window_seconds=60.0,
        clock=clock,
    )
    monkeypatch.setattr(auth_router, "login_attempt_limiter", limiter)
    return limiter, clock


def test_username_lockout_duration_increases_after_repeated_lockouts():
    clock = FakeClock()
    limiter = LoginAttemptLimiter(
        username_failure_limit=1,
        username_window_seconds=60.0,
        base_lockout_seconds=10.0,
        max_lockout_seconds=20.0,
        ip_failure_limit=100,
        ip_window_seconds=60.0,
        clock=clock,
    )

    first = limiter.evaluate(
        source_ip="203.0.113.20",
        username="repeat-target",
        credentials_valid=False,
    )
    assert first.reason == "username_lockout"

    clock.advance(11.0)
    second = limiter.evaluate(
        source_ip="203.0.113.20",
        username="repeat-target",
        credentials_valid=False,
    )
    assert second.reason == "username_lockout"

    clock.advance(11.0)
    still_locked = limiter.evaluate(
        source_ip="203.0.113.20",
        username="repeat-target",
        credentials_valid=True,
    )
    assert still_locked.allowed is False

    clock.advance(10.0)
    recovered = limiter.evaluate(
        source_ip="203.0.113.20",
        username="repeat-target",
        credentials_valid=True,
    )
    assert recovered.allowed is True


async def _create_user(
    db_session: AsyncSession,
    username: str,
    password: str,
) -> User:
    user = User(
        username=username,
        hashed_password=get_password_hash(password),
        role=UserRole.ADMIN.value,
        tenant_id="tenant-login-tests",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


async def _login(
    client: AsyncClient,
    username: str,
    password: str,
    source_ip: str = "203.0.113.10",
):
    return await client.post(
        "/api/v1/auth/token",
        data={"username": username, "password": password},
        headers={"X-Real-IP": source_ip},
    )


@pytest.mark.asyncio
async def test_username_lockout_does_not_affect_another_username(
    anon_client: AsyncClient,
    db_session: AsyncSession,
    login_guard,
):
    await _create_user(db_session, "locked-operator", "correct-one")
    await _create_user(db_session, "other-operator", "correct-two")

    for _ in range(3):
        response = await _login(anon_client, "locked-operator", "wrong")
        assert response.status_code == 401

    locked = await _login(anon_client, "locked-operator", "correct-one")
    unaffected = await _login(anon_client, "other-operator", "correct-two")

    assert locked.status_code == 401
    assert locked.json()["detail"] == "Incorrect username or password"
    assert unaffected.status_code == 200


@pytest.mark.asyncio
async def test_correct_password_authenticates_after_lockout_expires(
    anon_client: AsyncClient,
    db_session: AsyncSession,
    login_guard,
):
    _, clock = login_guard
    await _create_user(db_session, "recovering-operator", "correct-password")

    for _ in range(3):
        await _login(anon_client, "recovering-operator", "wrong")

    assert (
        await _login(anon_client, "recovering-operator", "correct-password")
    ).status_code == 401

    clock.advance(31.0)

    assert (
        await _login(anon_client, "recovering-operator", "correct-password")
    ).status_code == 200


@pytest.mark.asyncio
async def test_per_ip_limit_blocks_account_spray_but_not_another_ip(
    anon_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
):
    limiter = LoginAttemptLimiter(
        username_failure_limit=100,
        username_window_seconds=60.0,
        base_lockout_seconds=30.0,
        max_lockout_seconds=120.0,
        ip_failure_limit=3,
        ip_window_seconds=60.0,
        clock=FakeClock(),
    )
    monkeypatch.setattr(auth_router, "login_attempt_limiter", limiter)
    await _create_user(db_session, "spray-target", "correct-password")

    for username in ("guess-one", "guess-two", "guess-three"):
        assert (await _login(anon_client, username, "wrong")).status_code == 401

    blocked = await _login(anon_client, "spray-target", "correct-password")
    allowed = await _login(
        anon_client,
        "spray-target",
        "correct-password",
        source_ip="203.0.113.11",
    )

    assert blocked.status_code == 401
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_unknown_and_known_users_both_perform_password_verification(
    anon_client: AsyncClient,
    db_session: AsyncSession,
    login_guard,
    monkeypatch,
):
    known = await _create_user(db_session, "known-operator", "correct-password")
    verified_hashes: list[str] = []

    def record_verification(_password: str, password_hash: str) -> bool:
        verified_hashes.append(password_hash)
        return False

    monkeypatch.setattr(auth_router, "verify_password", record_verification)

    await _login(anon_client, "missing-operator", "wrong")
    await _login(anon_client, known.username, "wrong")

    assert len(verified_hashes) == 2
    assert auth_router.DUMMY_PASSWORD_HASH in verified_hashes
    assert known.hashed_password in verified_hashes


@pytest.mark.asyncio
async def test_failed_login_logs_username_and_source_ip_without_password(
    anon_client: AsyncClient,
    login_guard,
    caplog,
):
    with caplog.at_level(logging.WARNING, logger=auth_router.__name__):
        response = await _login(
            anon_client,
            "audit-operator",
            "password-must-not-be-logged",
            source_ip="198.51.100.7",
        )

    assert response.status_code == 401
    assert "audit-operator" in caplog.text
    assert "198.51.100.7" in caplog.text
    assert "password-must-not-be-logged" not in caplog.text
