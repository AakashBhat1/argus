"""
Async Inference Worker Pool
=============================

Decouples frame acquisition from inference via an asyncio queue and
ThreadPoolExecutor. Implements dynamic micro-batching to amortize
per-inference overhead across multiple camera streams.

Architecture:
    Camera streams → asyncio.Queue → BatchCollector → ThreadPoolExecutor
                                                          ↓
                                              detector.detect_batch()
                                                          ↓
                                              asyncio.Future (per frame)

Design decisions:
- ThreadPoolExecutor (not multiprocessing): OpenVINO's C++ backend releases
  the GIL during inference. Threads share the compiled model with zero
  serialization overhead and lower RAM usage than separate processes.
- Micro-batching (configurable window): Frames from N cameras are collected
  for up to `batch_timeout_ms` or until `max_batch_size` frames accumulate,
  whichever comes first. This balances latency vs. throughput.
- Backpressure: When the queue is full, the oldest frame is dropped and
  a warning is logged. This prevents OOM under sustained overload.
"""

import asyncio
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional, Protocol

import numpy as np

from app.services.metrics import inference_metrics

logger = logging.getLogger(__name__)


class BatchDetector(Protocol):
    def detect_batch(self, frames: list[np.ndarray]) -> list[list[dict]]:
        ...


@dataclass
class InferenceRequest:
    """A single frame submitted for inference."""
    frame: np.ndarray
    camera_id: str
    timestamp: float = field(default_factory=time.time)
    future: asyncio.Future = field(default=None)


class InferenceWorkerPool:
    """
    Manages async inference with batching and thread-pool execution.

    Usage:
        pool = InferenceWorkerPool(detector)
        await pool.start()

        # From any async context (e.g., stream processing loop):
        detections = await pool.submit(frame, camera_id="cam-01")

        await pool.shutdown()
    """

    def __init__(
        self,
        detector: BatchDetector,
        num_workers: int = 2,
        max_batch_size: int = 8,
        batch_timeout_ms: float = 20.0,
        queue_max_size: int = 64,
    ):
        self._detector = detector
        self._num_workers = num_workers
        self._max_batch_size = max_batch_size
        self._batch_timeout_s = batch_timeout_ms / 1000.0
        self._queue_max_size = queue_max_size

        self._queue: asyncio.Queue[InferenceRequest] = None
        self._executor: ThreadPoolExecutor = None
        self._collector_task: Optional[asyncio.Task] = None
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Metrics
        self._batches_processed = 0
        self._frames_processed = 0
        self._frames_dropped = 0
        self._total_batch_latency_ms = 0.0
        self._last_batch_size = 0
        self._last_batch_latency_ms = 0.0

    async def start(self):
        """Start the worker pool. Call from FastAPI lifespan startup."""
        if self._running:
            return

        self._loop = asyncio.get_event_loop()
        self._queue = asyncio.Queue(maxsize=self._queue_max_size)
        self._executor = ThreadPoolExecutor(
            max_workers=self._num_workers,
            thread_name_prefix="ov-inference",
        )
        self._running = True
        self._collector_task = asyncio.create_task(self._batch_collector_loop())
        inference_metrics.update_queue_depth(0)
        inference_metrics.update_frames_dropped(0)

        logger.info(
            f"InferenceWorkerPool started: "
            f"workers={self._num_workers}, "
            f"batch_size={self._max_batch_size}, "
            f"timeout={self._batch_timeout_s * 1000:.0f}ms, "
            f"queue_max={self._queue_max_size}"
        )

    async def shutdown(self):
        """Gracefully stop the worker pool."""
        self._running = False

        if self._collector_task:
            self._collector_task.cancel()
            try:
                await self._collector_task
            except asyncio.CancelledError:
                pass

        if self._executor:
            self._executor.shutdown(wait=True)

        inference_metrics.update_queue_depth(0)
        inference_metrics.update_frames_dropped(self._frames_dropped)

        logger.info(
            f"InferenceWorkerPool stopped: "
            f"processed={self._frames_processed}, "
            f"dropped={self._frames_dropped}, "
            f"batches={self._batches_processed}"
        )

    async def submit(
        self, frame: np.ndarray, camera_id: str
    ) -> list[dict]:
        """
        Submit a frame for async inference.

        Non-blocking from the caller's perspective. Returns a list of
        detections when inference completes.

        Args:
            frame: BGR image as numpy array.
            camera_id: Camera identifier for metrics/logging.

        Returns:
            List of detection dicts (same format as detector.detect()).

        Raises:
            RuntimeError: If the worker pool is not running.
        """
        if not self._running:
            raise RuntimeError("InferenceWorkerPool is not running")

        request = InferenceRequest(
            frame=frame,
            camera_id=camera_id,
            future=self._loop.create_future(),
        )

        try:
            self._queue.put_nowait(request)
            inference_metrics.update_queue_depth(self.queue_depth)
        except asyncio.QueueFull:
            # Backpressure: drop the frame and warn
            self._frames_dropped += 1
            inference_metrics.update_frames_dropped(self._frames_dropped)
            inference_metrics.update_queue_depth(self.queue_depth)
            if self._frames_dropped % 100 == 1:
                logger.warning(
                    f"Inference queue full ({self._queue_max_size}). "
                    f"Dropped {self._frames_dropped} frames total. "
                    f"Consider increasing queue_max_size or reducing streams."
                )
            # Return empty detections instead of blocking
            return []

        # Wait for the batch collector to process this frame
        result = await request.future
        return result

    async def _batch_collector_loop(self):
        """
        Continuously collect frames into micro-batches and dispatch them
        to the thread pool for inference.

        Collection strategy:
          - Wait up to `batch_timeout_s` for frames to arrive
          - Dispatch when either `max_batch_size` frames collected OR timeout
          - Minimum batch size: 1 (no waiting with empty batch)
        """
        try:
            while self._running:
                batch: list[InferenceRequest] = []
                deadline = time.monotonic() + self._batch_timeout_s

                # Collect frames until batch full or timeout
                while len(batch) < self._max_batch_size:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break

                    try:
                        request = await asyncio.wait_for(
                            self._queue.get(),
                            timeout=remaining,
                        )
                        batch.append(request)
                        inference_metrics.update_queue_depth(self.queue_depth)
                    except asyncio.TimeoutError:
                        break

                if not batch:
                    # No frames in this window; yield control
                    await asyncio.sleep(0.001)
                    continue

                # Dispatch batch to thread pool
                await self._dispatch_batch(batch)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(f"Batch collector error: {e}")

    async def _dispatch_batch(self, batch: list[InferenceRequest]):
        """
        Send a batch of frames to the thread pool for inference,
        then resolve each request's Future with its results.
        """
        frames = [req.frame for req in batch]
        t_start = time.perf_counter()

        try:
            # Run inference in thread pool (GIL released during OpenVINO C++)
            all_detections = await self._loop.run_in_executor(
                self._executor,
                self._detector.detect_batch,
                frames,
            )

            # Resolve each request's future
            for i, request in enumerate(batch):
                if not request.future.done():
                    request.future.set_result(all_detections[i])

        except Exception as e:
            logger.error(f"Batch inference failed: {e}")
            # Resolve all futures with empty results on error
            for request in batch:
                if not request.future.done():
                    request.future.set_result([])

        # Update metrics
        elapsed_ms = (time.perf_counter() - t_start) * 1000
        self._batches_processed += 1
        self._frames_processed += len(batch)
        self._last_batch_size = len(batch)
        self._last_batch_latency_ms = elapsed_ms
        self._total_batch_latency_ms += elapsed_ms
        inference_metrics.record_batch(batch_size=len(batch), latency_ms=elapsed_ms)
        inference_metrics.update_queue_depth(self.queue_depth)
        inference_metrics.update_frames_dropped(self._frames_dropped)

    @property
    def queue_depth(self) -> int:
        """Current number of frames waiting in the queue."""
        return self._queue.qsize() if self._queue else 0

    @property
    def is_running(self) -> bool:
        return self._running

    def get_metrics(self) -> dict:
        """Return worker pool metrics for observability."""
        avg_batch_latency = (
            self._total_batch_latency_ms / self._batches_processed
            if self._batches_processed > 0
            else 0.0
        )
        return {
            "is_running": self._running,
            "num_workers": self._num_workers,
            "max_batch_size": self._max_batch_size,
            "batch_timeout_ms": self._batch_timeout_s * 1000,
            "queue_depth": self.queue_depth,
            "queue_max_size": self._queue_max_size,
            "batches_processed": self._batches_processed,
            "frames_processed": self._frames_processed,
            "frames_dropped": self._frames_dropped,
            "last_batch_size": self._last_batch_size,
            "last_batch_latency_ms": round(self._last_batch_latency_ms, 2),
            "avg_batch_latency_ms": round(avg_batch_latency, 2),
        }
