"""Timing and lifecycle rules shared by every camera stream loop."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Callable

logger = logging.getLogger(__name__)

# A reported rate outside this range is a container or RTSP artefact.
MIN_SANE_SOURCE_FPS = 1.0
MAX_SANE_SOURCE_FPS = 120.0
FALLBACK_SOURCE_FPS = 25.0

MAX_FRAME_SKIP = 6

# Latency, relative to the target, at which adaptive skipping steps up or down.
SKIP_UP_LATENCY_RATIO = 1.5
SKIP_DOWN_LATENCY_RATIO = 0.8

# A file further behind real time than this drops frames instead of analysing them.
MAX_FILE_LAG_SECONDS = 1.0
# After this much consecutive dropping, decoding itself cannot keep up.
MAX_DROPPED_SECONDS = 2.0

# Longer than the 10 s capture read timeout, so an in-flight read can finish.
STREAM_STOP_GRACE_SECONDS = 15.0


def sane_source_fps(reported: float) -> float:
    """Return the source's frame rate, or a fallback if the value is nonsense."""
    if not math.isfinite(reported) or not MIN_SANE_SOURCE_FPS <= reported <= MAX_SANE_SOURCE_FPS:
        return FALLBACK_SOURCE_FPS
    return float(reported)


def frame_skip_ceiling(source_fps: float, min_analysis_fps: float) -> int:
    """Largest skip that still analyses ``min_analysis_fps`` frames each second."""
    fps = sane_source_fps(source_fps)
    return max(1, min(MAX_FRAME_SKIP, int(fps / min_analysis_fps)))


def next_frame_skip(current: int, latency_ms: float, target_ms: float, ceiling: int) -> int:
    """One adaptive step: analyse less under load and more when there is headroom."""
    if latency_ms > target_ms * SKIP_UP_LATENCY_RATIO:
        return min(current + 1, ceiling)
    if latency_ms < target_ms * SKIP_DOWN_LATENCY_RATIO:
        return min(max(current - 1, 1), ceiling)
    return min(current, ceiling)


class FramePacer:
    """Holds a file source to its native frame rate, as a live camera would be.

    Dwell times and speeds are measured against the wall clock, so a file that
    decodes at full speed would report them several times too small.
    """

    def __init__(self, source_fps: float, clock: Callable[[], float] = time.monotonic):
        self._fps = sane_source_fps(source_fps)
        self._interval = 1.0 / self._fps
        self._clock = clock
        self._due: float | None = None
        self._consecutive_drops = 0

    def restart(self) -> None:
        """Forget the schedule, e.g. after a pause."""
        self._due = None
        self._consecutive_drops = 0

    def next_delay(self) -> float:
        """Seconds to wait until the next frame is due (0 when already late)."""
        now = self._clock()
        if self._due is None:
            self._due = now
        delay = self._due - now
        self._due += self._interval
        return max(0.0, delay)

    def should_drop(self) -> bool:
        """Whether to skip analysing this frame because playback is far behind."""
        now = self._clock()
        if self._due is None or now - self._due <= MAX_FILE_LAG_SECONDS:
            self._consecutive_drops = 0
            return False
        self._consecutive_drops += 1
        if self._consecutive_drops > MAX_DROPPED_SECONDS * self._fps:
            # Decoding alone is slower than real time: stop chasing the clock.
            self._due = now
            self._consecutive_drops = 0
            return False
        return True


async def wait_for_stream_exit(task: asyncio.Task, label: str) -> bool:
    """Let a stream loop leave on its own; return whether it finished cleanly.

    Cancelling the loop would abandon a worker thread still inside native
    capture or tracker code, so it gets a grace period first. The caller must
    have cleared the loop's running flag.
    """
    done, _ = await asyncio.wait({task}, timeout=STREAM_STOP_GRACE_SECONDS)
    if done:
        return True
    logger.warning("Stream %s did not stop within %.0fs; cancelling it", label, STREAM_STOP_GRACE_SECONDS)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return False
