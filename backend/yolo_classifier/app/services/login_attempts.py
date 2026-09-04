"""In-process failed-login throttling for the single-worker API deployment."""

from __future__ import annotations

from collections import deque
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

    def reset(self) -> None:
        """Clear all limiter state, primarily for isolated tests."""
        with self._lock:
            self._username_states.clear()
            self._ip_failures.clear()
            self._evaluations_since_sweep = 0


login_attempt_limiter = LoginAttemptLimiter()
