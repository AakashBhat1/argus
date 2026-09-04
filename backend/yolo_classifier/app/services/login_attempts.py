"""In-process failed-login throttling for the single-worker API deployment."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import Callable, Deque


@dataclass(frozen=True)
class LoginDecision:
    allowed: bool
    reason: str


@dataclass
class _UsernameState:
    failures: Deque[float] = field(default_factory=deque)
    locked_until: float = 0.0
    lockout_level: int = 0


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
    ) -> None:
        if username_failure_limit < 1 or ip_failure_limit < 1:
            raise ValueError("Login failure limits must be positive")
        self._username_failure_limit = username_failure_limit
        self._username_window_seconds = username_window_seconds
        self._base_lockout_seconds = base_lockout_seconds
        self._max_lockout_seconds = max_lockout_seconds
        self._ip_failure_limit = ip_failure_limit
        self._ip_window_seconds = ip_window_seconds
        self._clock = clock
        self._username_states: dict[str, _UsernameState] = {}
        self._ip_failures: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()

    @staticmethod
    def _username_key(username: str) -> str:
        return username[:255].casefold()

    @staticmethod
    def _prune(timestamps: Deque[float], cutoff: float) -> None:
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

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
            state = self._username_states.setdefault(username_key, _UsernameState())
            ip_failures = self._ip_failures[source_ip]
            self._prune(
                state.failures,
                now - self._username_window_seconds,
            )
            self._prune(ip_failures, now - self._ip_window_seconds)

            if state.locked_until > now:
                return LoginDecision(False, "username_lockout")
            if len(ip_failures) >= self._ip_failure_limit:
                return LoginDecision(False, "ip_rate_limit")

            if credentials_valid:
                self._username_states.pop(username_key, None)
                return LoginDecision(True, "authenticated")

            state.failures.append(now)
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

    def reset(self) -> None:
        """Clear all limiter state, primarily for isolated tests."""
        with self._lock:
            self._username_states.clear()
            self._ip_failures.clear()


login_attempt_limiter = LoginAttemptLimiter()
