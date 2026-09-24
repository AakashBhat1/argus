"""
Inference Metrics & Observability
==================================

Lightweight in-process metrics collection for the inference pipeline.
Provides both JSON and Prometheus text format output.

No external dependencies (no prometheus_client). Uses atomic Python
operations for thread safety in the common case.

Tracked metrics:
  - inference_latency_seconds (histogram approximation)
  - frames_processed_total (counter per camera)
  - fps_per_stream (gauge per camera)
  - queue_depth (gauge)
  - batch_size (histogram approximation)
  - throughput_fps (gauge, global)
  - device_info (info)
"""

import time
import threading
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class _SlidingWindowCounter:
    """
    A simple sliding window counter for rate calculations.
    Thread-safe via a lock.
    """

    def __init__(self, window_seconds: float = 60.0):
        self._window = window_seconds
        self._events: list[float] = []
        self._lock = threading.Lock()

    def record(self, timestamp: float = None):
        t = timestamp or time.time()
        with self._lock:
            self._events.append(t)
            self._cleanup(t)

    def rate(self) -> float:
        """Events per second over the sliding window."""
        now = time.time()
        with self._lock:
            self._cleanup(now)
            if not self._events:
                return 0.0
            elapsed = now - self._events[0]
            if elapsed <= 0:
                return 0.0
            return len(self._events) / elapsed

    def count(self) -> int:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            return len(self._events)

    def _cleanup(self, now: float):
        cutoff = now - self._window
        while self._events and self._events[0] < cutoff:
            self._events.pop(0)


class _LatencyTracker:
    """Tracks latency with min/max/avg over a sliding window."""

    def __init__(self, window_seconds: float = 60.0):
        self._window = window_seconds
        self._samples: list[tuple[float, float]] = []  # (timestamp, value_ms)
        self._lock = threading.Lock()

    def record(self, value_ms: float):
        now = time.time()
        with self._lock:
            self._samples.append((now, value_ms))
            self._cleanup(now)

    def stats(self) -> dict:
        now = time.time()
        with self._lock:
            self._cleanup(now)
            if not self._samples:
                return {"min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0, "count": 0}
            values = [s[1] for s in self._samples]

        values_sorted = sorted(values)
        n = len(values_sorted)
        return {
            "min": round(values_sorted[0], 2),
            "max": round(values_sorted[-1], 2),
            "avg": round(sum(values_sorted) / n, 2),
            "p50": round(values_sorted[n // 2], 2),
            "p95": round(values_sorted[int(n * 0.95)], 2) if n > 1 else round(values_sorted[0], 2),
            "count": n,
        }

    def _cleanup(self, now: float):
        cutoff = now - self._window
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.pop(0)


class InferenceMetrics:
    """
    Central metrics registry for the inference pipeline.

    Usage:
        metrics = InferenceMetrics()
        metrics.record_inference(camera_id="cam-01", latency_ms=23.5)
        metrics.record_batch(batch_size=4, latency_ms=45.2)

        data = metrics.get_metrics()        # JSON dict
        text = metrics.format_prometheus()   # Prometheus exposition format
    """

    def __init__(self, window_seconds: float = 60.0):
        self._window = window_seconds

        # Per-camera frame counters
        self._camera_frames: dict[str, _SlidingWindowCounter] = defaultdict(
            lambda: _SlidingWindowCounter(window_seconds)
        )

        # Global counters
        self._total_frames = _SlidingWindowCounter(window_seconds)
        self._total_inferences = 0
        self._total_frames_all_time = 0

        # Latency trackers
        self._inference_latency = _LatencyTracker(window_seconds)
        self._batch_latency = _LatencyTracker(window_seconds)
        self._preprocess_latency = _LatencyTracker(window_seconds)
        self._postprocess_latency = _LatencyTracker(window_seconds)

        # Batch size tracker
        self._batch_sizes = _LatencyTracker(window_seconds)

        # Queue depth (set externally)
        self._queue_depth = 0
        self._frames_dropped = 0

        # Device info
        self._device_name = "unknown"
        self._model_path = "unknown"

        self._start_time = time.time()

    def set_device_info(self, device: str, model_path: str):
        """Set static device information at startup."""
        self._device_name = device
        self._model_path = model_path

    def record_inference(
        self,
        camera_id: str,
        latency_ms: float,
        preprocess_ms: float = 0,
        postprocess_ms: float = 0,
    ):
        """Record a completed single-frame inference."""
        self._camera_frames[camera_id].record()
        self._total_frames.record()
        self._total_inferences += 1
        self._total_frames_all_time += 1
        self._inference_latency.record(latency_ms)
        if preprocess_ms > 0:
            self._preprocess_latency.record(preprocess_ms)
        if postprocess_ms > 0:
            self._postprocess_latency.record(postprocess_ms)

    def record_batch(self, batch_size: int, latency_ms: float):
        """Record a completed batch inference."""
        self._batch_latency.record(latency_ms)
        self._batch_sizes.record(float(batch_size))

    def update_queue_depth(self, depth: int):
        """Update the current inference queue depth."""
        self._queue_depth = depth

    def update_frames_dropped(self, count: int):
        """Update total frames dropped due to backpressure."""
        self._frames_dropped = count

    def get_fps_for_camera(self, camera_id: str) -> float:
        """Get current FPS for a specific camera."""
        if camera_id in self._camera_frames:
            return round(self._camera_frames[camera_id].rate(), 1)
        return 0.0

    def get_metrics(self) -> dict:
        """Return all metrics as a JSON-serializable dict."""
        uptime = time.time() - self._start_time

        per_camera_fps = {}
        for cam_id, counter in self._camera_frames.items():
            per_camera_fps[cam_id] = round(counter.rate(), 1)

        return {
            "uptime_seconds": round(uptime, 1),
            "device": self._device_name,
            "model": self._model_path,
            "throughput": {
                "global_fps": round(self._total_frames.rate(), 1),
                "total_frames_processed": self._total_frames_all_time,
                "total_inferences": self._total_inferences,
                "per_camera_fps": per_camera_fps,
            },
            "latency": {
                "inference": self._inference_latency.stats(),
                "batch": self._batch_latency.stats(),
                "preprocess": self._preprocess_latency.stats(),
                "postprocess": self._postprocess_latency.stats(),
            },
            "batching": {
                "batch_size": self._batch_sizes.stats(),
            },
            "queue": {
                "depth": self._queue_depth,
                "frames_dropped": self._frames_dropped,
            },
        }

    def format_prometheus(self) -> str:
        """
        Format metrics in Prometheus exposition format for scraping.

        Example output:
            # HELP surveillance_inference_latency_ms Inference latency in milliseconds
            # TYPE surveillance_inference_latency_ms gauge
            surveillance_inference_latency_avg_ms 23.5
        """
        lines = []
        m = self.get_metrics()

        # Throughput
        lines.append("# HELP surveillance_throughput_fps Global frames per second")
        lines.append("# TYPE surveillance_throughput_fps gauge")
        lines.append(f'surveillance_throughput_fps {m["throughput"]["global_fps"]}')

        lines.append("# HELP surveillance_frames_total Total frames processed")
        lines.append("# TYPE surveillance_frames_total counter")
        lines.append(
            f'surveillance_frames_total {m["throughput"]["total_frames_processed"]}'
        )

        # Per-camera FPS
        lines.append("# HELP surveillance_camera_fps Frames per second per camera")
        lines.append("# TYPE surveillance_camera_fps gauge")
        for cam_id, fps in m["throughput"]["per_camera_fps"].items():
            lines.append(f'surveillance_camera_fps{{camera_id="{cam_id}"}} {fps}')

        # Inference latency
        lat = m["latency"]["inference"]
        lines.append("# HELP surveillance_inference_latency_ms Inference latency")
        lines.append("# TYPE surveillance_inference_latency_ms gauge")
        lines.append(f'surveillance_inference_latency_avg_ms {lat["avg"]}')
        lines.append(f'surveillance_inference_latency_p50_ms {lat["p50"]}')
        lines.append(f'surveillance_inference_latency_p95_ms {lat["p95"]}')

        # Queue
        lines.append("# HELP surveillance_queue_depth Current inference queue depth")
        lines.append("# TYPE surveillance_queue_depth gauge")
        lines.append(f'surveillance_queue_depth {m["queue"]["depth"]}')

        lines.append("# HELP surveillance_frames_dropped Total frames dropped")
        lines.append("# TYPE surveillance_frames_dropped counter")
        lines.append(f'surveillance_frames_dropped {m["queue"]["frames_dropped"]}')

        # Device info
        lines.append("# HELP surveillance_device_info Inference device information")
        lines.append("# TYPE surveillance_device_info gauge")
        lines.append(
            f'surveillance_device_info{{device="{m["device"]}",'
            f'model="{m["model"]}"}} 1'
        )

        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
inference_metrics = InferenceMetrics()
