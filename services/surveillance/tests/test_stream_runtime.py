"""Capture safety, file pacing and frame-skip control for video streams."""

from __future__ import annotations

import asyncio
import math
import threading
import time
import uuid
from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest

from app.models import Camera
from app.services.stream_manager import VideoStream
from argus_vision.inference_worker import inference_worker_count
from argus_vision.sources import GuardedCapture
from argus_vision.stream_runtime import FramePacer, frame_skip_ceiling, next_frame_skip

FRAME = np.zeros((64, 64, 3), dtype=np.uint8)


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class _RaceDetectingCapture:
    """Records a release that lands while a read is still inside the capture."""

    def __init__(self, read_seconds: float = 0.02, fps: float = 1000.0):
        self.read_seconds = read_seconds
        self.fps = fps
        self.reading = 0
        self.overlap = False
        self.releases = 0
        self.opened = True
        self.seeks: list[float] = []
        self.position = 0

    def isOpened(self) -> bool:
        return self.opened

    def read(self):
        self.reading += 1
        time.sleep(self.read_seconds)
        self.reading -= 1
        self.position += 1
        return True, FRAME.copy()

    def grab(self) -> bool:
        self.position += 1
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self.fps
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return self.position
        return 0.0

    def set(self, prop, value) -> bool:
        if prop == cv2.CAP_PROP_POS_FRAMES:
            self.seeks.append(value)
        return True

    def release(self) -> None:
        if self.reading:
            self.overlap = True
        self.releases += 1
        self.opened = False


def _stream(url: str, mock_inference_pool) -> VideoStream:
    unique = uuid.uuid4().hex
    camera = Camera(id=f"cam-{unique}", name="Yard", location="North", stream_url=url, tenant_id=f"t-{unique}")
    stream = VideoStream(camera, mock_inference_pool)
    stream._store_detections = AsyncMock()
    stream._check_alerts = AsyncMock()
    return stream


async def _wait_until(condition, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.01)


# -- GuardedCapture ---------------------------------------------------------


def test_guarded_capture_never_releases_during_a_read():
    raw = _RaceDetectingCapture(read_seconds=0.001)
    capture = GuardedCapture(raw)

    def reader():
        while capture.read()[0]:
            pass

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    time.sleep(0.02)
    capture.release()
    thread.join(2)
    assert not thread.is_alive()
    assert raw.overlap is False


def test_guarded_capture_release_is_idempotent_and_read_after_it_fails_cleanly():
    raw = _RaceDetectingCapture(read_seconds=0)
    capture = GuardedCapture(raw)
    capture.release()
    capture.release()
    assert raw.releases == 1
    assert capture.read() == (False, None)
    assert capture.grab() is False
    assert capture.isOpened() is False


# -- FramePacer -------------------------------------------------------------


def test_pacer_holds_frames_to_the_source_rate():
    clock = _Clock()
    pacer = FramePacer(10.0, clock=clock)
    assert pacer.next_delay() == 0.0
    assert pacer.next_delay() == pytest.approx(0.1)
    clock.now += 0.1
    assert pacer.next_delay() == pytest.approx(0.1)


def test_pacer_never_asks_for_a_negative_wait_when_late():
    clock = _Clock()
    pacer = FramePacer(10.0, clock=clock)
    pacer.next_delay()
    clock.now += 5.0
    assert pacer.next_delay() == 0.0


def test_pacer_drops_frames_while_far_behind_then_recovers():
    clock = _Clock()
    pacer = FramePacer(10.0, clock=clock)
    pacer.next_delay()
    assert pacer.should_drop() is False
    clock.now += 3.0
    pacer.next_delay()
    assert pacer.should_drop() is True
    clock.now -= 3.0
    pacer.next_delay()
    assert pacer.should_drop() is False


def test_pacer_stops_chasing_when_decoding_cannot_catch_up():
    clock = _Clock()
    pacer = FramePacer(10.0, clock=clock)
    pacer.next_delay()
    clock.now += 60.0
    dropped = 0
    for _ in range(1000):
        pacer.next_delay()
        if not pacer.should_drop():
            break
        dropped += 1
    assert 0 < dropped < 1000


@pytest.mark.parametrize("bad_fps", [0.0, -5.0, math.nan, math.inf, 90000.0])
def test_pacer_survives_a_nonsense_reported_frame_rate(bad_fps):
    clock = _Clock()
    pacer = FramePacer(bad_fps, clock=clock)
    pacer.next_delay()
    assert 0.0 < pacer.next_delay() <= 1.0


# -- Frame skip -------------------------------------------------------------


@pytest.mark.parametrize(
    ("source_fps", "ceiling"),
    [(3.0, 1), (5.0, 1), (12.0, 3), (24.0, 6), (30.0, 6), (120.0, 6), (0.0, 6), (math.nan, 6)],
)
def test_ceiling_keeps_enough_frames_for_the_tracker(source_fps, ceiling):
    assert frame_skip_ceiling(source_fps, min_analysis_fps=4.0) == ceiling


def test_skip_falls_at_ordinary_cpu_latency():
    assert next_frame_skip(6, latency_ms=67, target_ms=100, ceiling=6) == 5


def test_skip_rises_under_load_but_not_past_the_ceiling():
    assert next_frame_skip(2, latency_ms=400, target_ms=100, ceiling=3) == 3
    assert next_frame_skip(3, latency_ms=400, target_ms=100, ceiling=3) == 3


def test_skip_holds_between_the_two_thresholds():
    assert next_frame_skip(3, latency_ms=120, target_ms=100, ceiling=6) == 3


def test_skip_is_never_below_one():
    assert next_frame_skip(1, latency_ms=1, target_ms=100, ceiling=6) == 1


def test_a_lowered_ceiling_pulls_the_skip_down():
    assert next_frame_skip(6, latency_ms=120, target_ms=100, ceiling=3) == 3


# -- Worker count -----------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "optimal", "expected"), [(0, 6, 6), (0, 1, 2), (3, 6, 3), (1, 8, 1)]
)
def test_worker_count_is_taken_from_settings_or_the_model(configured, optimal, expected):
    assert inference_worker_count(configured, optimal) == expected


# -- VideoStream ------------------------------------------------------------


async def test_stop_waits_for_the_read_in_flight(mock_inference_pool, monkeypatch):
    captures: list[_RaceDetectingCapture] = []

    def opener(_url):
        captures.append(_RaceDetectingCapture(read_seconds=0.05))
        return captures[-1]

    monkeypatch.setattr("app.services.stream_manager._open_capture", opener)
    stream = _stream("rtsp://203.0.113.20/live", mock_inference_pool)
    assert await stream.start()
    await _wait_until(lambda: len(captures) == 2 and captures[-1].reading > 0)
    await stream.stop()
    assert captures[-1].overlap is False
    assert captures[-1].releases == 1


async def test_a_file_source_plays_at_its_own_frame_rate(mock_inference_pool, monkeypatch):
    capture = _RaceDetectingCapture(read_seconds=0, fps=20.0)
    monkeypatch.setattr("app.services.stream_manager._open_capture", lambda _url: capture)
    capture.release = lambda: None  # the same object serves the preflight open
    stream = _stream("video://clip.mp4", mock_inference_pool)
    assert await stream.start()
    await asyncio.sleep(0.5)
    await stream.stop()
    assert 6 <= stream._frame_count <= 14


async def test_pause_releases_on_the_stream_loop_and_resume_seeks_back(mock_inference_pool, monkeypatch):
    captures: list[_RaceDetectingCapture] = []

    def opener(_url):
        captures.append(_RaceDetectingCapture(read_seconds=0.001, fps=500.0))
        return captures[-1]

    monkeypatch.setattr("app.services.stream_manager._open_capture", opener)
    stream = _stream("video://clip.mp4", mock_inference_pool)
    assert await stream.start()
    await _wait_until(lambda: len(captures) == 2 and captures[-1].position > 3)
    running = captures[-1]

    stream.pause()
    await _wait_until(lambda: running.releases == 1)
    assert running.overlap is False
    stream.resume()
    await _wait_until(lambda: len(captures) == 3 and bool(captures[-1].seeks))
    assert captures[-1].seeks[0] == running.position
    await stream.stop()


async def test_resume_without_pause_opens_nothing(mock_inference_pool, monkeypatch):
    captures: list[_RaceDetectingCapture] = []

    def opener(_url):
        captures.append(_RaceDetectingCapture(read_seconds=0.01))
        return captures[-1]

    monkeypatch.setattr("app.services.stream_manager._open_capture", opener)
    stream = _stream("rtsp://203.0.113.20/live", mock_inference_pool)
    assert await stream.start()
    await _wait_until(lambda: len(captures) == 2)
    stream.resume()
    await asyncio.sleep(0.05)
    assert len(captures) == 2
    await stream.stop()


async def test_a_slow_source_does_not_get_starved_by_the_default_skip(mock_inference_pool, monkeypatch):
    capture = _RaceDetectingCapture(read_seconds=0, fps=12.0)
    monkeypatch.setattr("app.services.stream_manager._open_capture", lambda _url: capture)
    capture.release = lambda: None
    stream = _stream("video://clip.mp4", mock_inference_pool)
    assert await stream.start()
    await _wait_until(lambda: stream._frame_count > 0)
    assert stream._current_frame_skip <= 3
    await stream.stop()
