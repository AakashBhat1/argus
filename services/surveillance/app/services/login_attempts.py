"""Failed-login throttling: per-IP rate limits and progressive username lockouts.

``DatabaseLoginLimiter`` (the default) keeps the counters in the database, so
every worker process and every replica enforces one shared limit. Usernames
and IP addresses are stored only as keyed hashes (HMAC with SECRET_KEY).
``LoginAttemptLimiter`` applies the same policy in process memory; tests use
it for its injectable clock.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Callable, Deque, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LoginFailure, LoginLockout


@dataclass(frozen=True)
class LoginDecision:
    allowed: bool
    reason: str


@dataclass
class _UsernameState:
    failures: Deque[float] = field(default_factory=deque)
    locked_until: float = 0.0
    lockout_level: int = 0
    last_failure_at: float = 0.0


class LoginAttemptLimiter:
    """Atomically enforce per-IP limits and progressive username lockouts."""

    def __init__(
        self,
        *,
        username_failure_limit: int = 5,
        username_window_seconds: float = 15 * 60,
        base_lockout_seconds: float = 60,
        max_lockout_seconds: float = 15 * 60,
        ip_failure_limit: int = 20,
        ip_window_seconds: float = 60,
        clock: Callable[[], float] = monotonic,
        sweep_interval: int = 128,
    ) -> None:
        if (
            username_failure_limit < 1
            or ip_failure_limit < 1
            or sweep_interval < 1
        ):
            raise ValueError("Login failure limits must be positive")
        self._username_failure_limit = username_failure_limit
        self._username_window_seconds = username_window_seconds
        self._base_lockout_seconds = base_lockout_seconds
        self._max_lockout_seconds = max_lockout_seconds
        self._ip_failure_limit = ip_failure_limit
        self._ip_window_seconds = ip_window_seconds
        self._clock = clock
        self._sweep_interval = sweep_interval
        self._evaluations_since_sweep = 0
        self._username_states: dict[str, _UsernameState] = {}
        self._ip_failures: dict[str, Deque[float]] = {}
        self._lock = Lock()

    @staticmethod
    def _username_key(username: str) -> str:
        return username[:255].casefold()

    @staticmethod
    def _prune(timestamps: Deque[float], cutoff: float) -> None:
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

    def _username_state_expired(self, state: _UsernameState, now: float) -> bool:
        retention_until = max(
            state.locked_until,
            state.last_failure_at + self._username_window_seconds,
        )
        return not state.failures and retention_until <= now

    def _sweep_expired(self, now: float) -> None:
        username_cutoff = now - self._username_window_seconds
        for username_key, state in list(self._username_states.items()):
            self._prune(state.failures, username_cutoff)
            if self._username_state_expired(state, now):
                del self._username_states[username_key]

        ip_cutoff = now - self._ip_window_seconds
        for source_ip, failures in list(self._ip_failures.items()):
            self._prune(failures, ip_cutoff)
            if not failures:
                del self._ip_failures[source_ip]

    def evaluate(
        self,
        *,
        source_ip: str,
        username: str,
        credentials_valid: bool,
    ) -> LoginDecision:
        """Return a decision and update failure state as one atomic operation."""
        now = self._clock()
        username_key = self._username_key(username)

        with self._lock:
            self._evaluations_since_sweep += 1
            if self._evaluations_since_sweep >= self._sweep_interval:
                self._sweep_expired(now)
                self._evaluations_since_sweep = 0

            state = self._username_states.get(username_key)
            if state is not None:
                self._prune(
                    state.failures,
                    now - self._username_window_seconds,
                )
                if self._username_state_expired(state, now):
                    del self._username_states[username_key]
                    state = None

            ip_failures = self._ip_failures.get(source_ip)
            if ip_failures is not None:
                self._prune(ip_failures, now - self._ip_window_seconds)
                if not ip_failures:
                    del self._ip_failures[source_ip]
                    ip_failures = None

            if state is not None and state.locked_until > now:
                return LoginDecision(False, "username_lockout")
            if (
                ip_failures is not None
                and len(ip_failures) >= self._ip_failure_limit
            ):
                return LoginDecision(False, "ip_rate_limit")

            if credentials_valid:
                self._username_states.pop(username_key, None)
                return LoginDecision(True, "authenticated")

            if state is None:
                state = _UsernameState()
                self._username_states[username_key] = state
            if ip_failures is None:
                ip_failures = deque()
                self._ip_failures[source_ip] = ip_failures

            state.failures.append(now)
            state.last_failure_at = now
            ip_failures.append(now)

            if len(state.failures) >= self._username_failure_limit:
                state.lockout_level += 1
                lockout_seconds = min(
                    self._base_lockout_seconds * (2 ** (state.lockout_level - 1)),
                    self._max_lockout_seconds,
                )
                state.locked_until = now + lockout_seconds
                state.failures.clear()
                return LoginDecision(False, "username_lockout")

            if len(ip_failures) >= self._ip_failure_limit:
                return LoginDecision(False, "ip_rate_limit")

            return LoginDecision(False, "invalid_credentials")

    async def decide(
        self, db: AsyncSession, *, source_ip: str, username: str, credentials_valid: bool
    ) -> LoginDecision:
        return self.evaluate(source_ip=source_ip, username=username, credentials_valid=credentials_valid)

    def reset(self) -> None:
        """Clear all limiter state, primarily for isolated tests."""
        with self._lock:
            self._username_states.clear()
            self._ip_failures.clear()
            self._evaluations_since_sweep = 0


def _default_secret() -> bytes:
    from app.services.auth import SECRET_KEY

    return SECRET_KEY.encode("utf-8")


class DatabaseLoginLimiter:
    """``LoginAttemptLimiter``'s policy with state shared through the database."""

    def __init__(
        self,
        *,
        username_failure_limit: int = 5,
        username_window_seconds: float = 15 * 60,
        base_lockout_seconds: float = 60,
        max_lockout_seconds: float = 15 * 60,
        ip_failure_limit: int = 20,
        ip_window_seconds: float = 60,
        clock: Callable[[], float] = time.time,
        secret: Optional[Callable[[], bytes]] = None,
        cleanup_interval: int = 256,
    ) -> None:
        if username_failure_limit < 1 or ip_failure_limit < 1 or cleanup_interval < 1:
            raise ValueError("Login failure limits must be positive")
        self._username_failure_limit = username_failure_limit
        self._username_window_seconds = username_window_seconds
        self._base_lockout_seconds = base_lockout_seconds
        self._max_lockout_seconds = max_lockout_seconds
        self._ip_failure_limit = ip_failure_limit
        self._ip_window_seconds = ip_window_seconds
        self._clock = clock
        self._secret = secret or _default_secret
        self._cleanup_interval = cleanup_interval
        self._decisions = 0

    def _subject(self, kind: str, value: str) -> str:
        return hmac.new(self._secret(), f"{kind}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()

    async def _count(self, db: AsyncSession, subject: str, since: float) -> int:
        return await db.scalar(
            select(func.count()).select_from(LoginFailure).where(
                LoginFailure.subject == subject, LoginFailure.failed_at > since
            )
        ) or 0

    async def _cleanup(self, db: AsyncSession, now: float) -> None:
        horizon = now - max(self._username_window_seconds, self._ip_window_seconds)
        await db.execute(delete(LoginFailure).where(LoginFailure.failed_at <= horizon))
        await db.execute(
            delete(LoginLockout).where(
                LoginLockout.locked_until <= now,
                LoginLockout.last_failure_at <= now - self._username_window_seconds,
            )
        )

    async def decide(
        self, db: AsyncSession, *, source_ip: str, username: str, credentials_valid: bool
    ) -> LoginDecision:
        """Decide and record the attempt; commits so failures persist even
        though the request itself ends in an error."""
        now = self._clock()
        user = self._subject("user", username[:255].casefold())
        ip = self._subject("ip", source_ip)

        self._decisions += 1
        if self._decisions % self._cleanup_interval == 0:
            await self._cleanup(db, now)

        lock = await db.get(LoginLockout, user, with_for_update=True)
        if (
            lock is not None
            and lock.locked_until <= now
            and lock.last_failure_at <= now - self._username_window_seconds
        ):
            # Quiet for a whole window: forget the lockout history.
            await db.delete(lock)
            lock = None

        if lock is not None and lock.locked_until > now:
            await db.commit()
            return LoginDecision(False, "username_lockout")
        ip_failures = await self._count(db, ip, now - self._ip_window_seconds)
        if ip_failures >= self._ip_failure_limit:
            await db.commit()
            return LoginDecision(False, "ip_rate_limit")

        if credentials_valid:
            await db.execute(delete(LoginFailure).where(LoginFailure.subject == user))
            if lock is not None:
                await db.delete(lock)
            await db.commit()
            return LoginDecision(True, "authenticated")

        db.add_all([LoginFailure(subject=user, failed_at=now), LoginFailure(subject=ip, failed_at=now)])
        if lock is None:
            lock = LoginLockout(subject=user, locked_until=0.0, lockout_level=0, last_failure_at=now)
            db.add(lock)
        lock.last_failure_at = now
        await db.flush()
        user_failures = await self._count(db, user, now - self._username_window_seconds)

        decision = LoginDecision(False, "invalid_credentials")
        if user_failures >= self._username_failure_limit:
            lock.lockout_level = (lock.lockout_level or 0) + 1
            lock.locked_until = now + min(
                self._base_lockout_seconds * (2 ** (lock.lockout_level - 1)), self._max_lockout_seconds
            )
            await db.execute(delete(LoginFailure).where(LoginFailure.subject == user))
            decision = LoginDecision(False, "username_lockout")
        elif ip_failures + 1 >= self._ip_failure_limit:
            decision = LoginDecision(False, "ip_rate_limit")
        try:
            await db.commit()
        except IntegrityError:
            # Another worker created the lockout row first; this attempt was
            # a failure either way.
            await db.rollback()
        return decision


login_attempt_limiter = DatabaseLoginLimiter()
