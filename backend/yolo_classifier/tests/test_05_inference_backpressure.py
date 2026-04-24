"""
TEST 5 - Inference Worker Backpressure / Frame Dropping

Tests verify that when the inference queue is full:
1. submit() returns [] (drops frame)
2. _frames_dropped increments
3. Pool continues processing after drops
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.inference_worker import InferenceWorkerPool


def _make_frame() -> np.ndarray:
    return np.zeros((480, 640, 3), dtype=np.uint8)


def _slow_detector(delay_s: float = 0.05):
    detector = MagicMock()

    def detect_batch(frames):
        time.sleep(delay_s)
        return [[] for _ in frames]

    detector.detect_batch = detect_batch
    return detector


class TestInferenceBackpressure:
    @pytest.mark.asyncio
    async def test_submit_before_start_raises(self):
        """Submitting before pool.start() must raise RuntimeError."""
        detector = _slow_detector(0.001)
        pool = InferenceWorkerPool(detector, num_workers=1, queue_max_size=4)
        with pytest.raises(RuntimeError, match="not running"):
            await pool.submit(_make_frame(), camera_id="cam-01")

    @pytest.mark.asyncio
    async def test_queue_full_returns_empty_and_counts_drop(self):
        """RED: With queue_max_size=1, second submit should drop."""
        detector = _slow_detector(0.5)
        pool = InferenceWorkerPool(
            detector,
            num_workers=1,
            max_batch_size=1,
            batch_timeout_ms=5,
            queue_max_size=1,
        )
        await pool.start()
        try:
            # Let the collector pick up frame 1 and block on the slow detector.
            task1 = asyncio.create_task(pool.submit(_make_frame(), "cam-01"))
            await asyncio.sleep(0.05)

            # Frame 2 fills the now-empty queue slot.
            task2 = asyncio.create_task(pool.submit(_make_frame(), "cam-02"))
            await asyncio.sleep(0)  # let task2 execute put_nowait

            # Frame 3 should trigger QueueFull and be dropped.
            result3 = await pool.submit(_make_frame(), "cam-03")

            assert result3 == [], f"Expected [] on queue-full drop, got {result3}"
            assert pool._frames_dropped >= 1

            await task1
            await task2
        finally:
            await pool.shutdown()

    @pytest.mark.asyncio
    async def test_pool_continues_after_drops(self):
        """After dropping frames the pool must still process new ones."""
        detector = MagicMock()
        detector.detect_batch = MagicMock(return_value=[[{"class": "person"}]])

        pool = InferenceWorkerPool(
            detector,
            num_workers=1,
            max_batch_size=1,
            batch_timeout_ms=5,
            queue_max_size=1,
        )
        await pool.start()
        try:
            for _ in range(5):
                await pool.submit(_make_frame(), "cam-spam")

            await asyncio.sleep(0.2)

            assert pool.is_running
            result = await pool.submit(_make_frame(), "cam-ok")
            assert isinstance(result, list)
        finally:
            await pool.shutdown()

    @pytest.mark.asyncio
    async def test_get_metrics_reflects_drops(self):
        """get_metrics() must expose frames_dropped accurately."""
        detector = _slow_detector(1.0)
        pool = InferenceWorkerPool(
            detector,
            num_workers=1,
            max_batch_size=1,
            batch_timeout_ms=5,
            queue_max_size=1,
        )
        await pool.start()
        try:
            # Frame 1: picked up by collector, blocks on slow detector
            t1 = asyncio.create_task(pool.submit(_make_frame(), "cam-01"))
            await asyncio.sleep(0.05)

            # Frame 2: fills the queue slot
            t2 = asyncio.create_task(pool.submit(_make_frame(), "cam-fill"))
            await asyncio.sleep(0)

            # Frames 3-5: each should be dropped (queue full)
            for _ in range(3):
                await pool.submit(_make_frame(), "cam-drop")

            metrics = pool.get_metrics()
            assert metrics["frames_dropped"] >= 3
            await t1
            await t2
        finally:
            await pool.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_stops_collector(self):
        """After shutdown(), is_running must be False."""
        detector = _slow_detector(0.001)
        pool = InferenceWorkerPool(detector, num_workers=1, queue_max_size=4)
        await pool.start()
        assert pool.is_running
        await pool.shutdown()
        assert not pool.is_running
        with pytest.raises(RuntimeError):
            await pool.submit(_make_frame(), "cam-dead")
