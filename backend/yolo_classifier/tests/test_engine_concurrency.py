"""OpenVINO detector hot path: request reuse and thread-safe metrics."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from app.detection import engine as engine_module
from app.detection.engine import OpenVINODetector


class _FakeRequest:
    def __init__(self, owner: "_FakeCompiled"):
        self.owner = owner
        self.thread = threading.get_ident()

    def infer(self, inputs):
        assert threading.get_ident() == self.thread, "request shared across threads"
        (blob,) = inputs.values()
        return {"out": np.full((blob.shape[0], 1), 7.0)}


class _FakeCompiled:
    def __init__(self, batch: int = 1):
        self.created = 0
        self.lock = threading.Lock()
        self.batch = batch

    def create_infer_request(self):
        with self.lock:
            self.created += 1
        return _FakeRequest(self)


class _Layer:
    def __init__(self, shape):
        self.shape = shape


@pytest.fixture
def detector(monkeypatch):
    compiled = _FakeCompiled()

    def fake_load(self):
        self._compiled_model = compiled
        self._input_layer = _Layer([1, 3, 640, 640])
        self._output_layer = "out"

    monkeypatch.setattr(OpenVINODetector, "_load_model", fake_load)
    det = OpenVINODetector()
    monkeypatch.setattr(
        det, "_preprocess", lambda frame: (np.zeros((1, 3, 640, 640), np.float32), {})
    )
    monkeypatch.setattr(
        det,
        "_preprocess_batch",
        lambda frames: (np.zeros((len(frames), 3, 640, 640), np.float32), [{}] * len(frames)),
    )
    monkeypatch.setattr(det, "_postprocess", lambda output, meta: [{"v": float(output.ravel()[0])}])
    return det, compiled


def test_infer_request_is_reused_per_thread(detector):
    det, compiled = detector
    frame = np.zeros((10, 10, 3), np.uint8)
    for _ in range(20):
        assert det.detect(frame) == [{"v": 7.0}]
    assert compiled.created == 1


def test_batch1_model_reuses_request_across_frames(detector):
    det, compiled = detector
    frames = [np.zeros((10, 10, 3), np.uint8)] * 6
    results = det.detect_batch(frames)
    assert len(results) == 6
    assert compiled.created == 1


def test_concurrent_workers_get_own_requests_and_exact_counts(detector):
    det, compiled = detector
    frame = np.zeros((10, 10, 3), np.uint8)
    workers, calls = 4, 200
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda _: det.detect(frame), range(calls)))
    assert compiled.created <= workers
    assert det.get_model_info()["total_inferences"] == calls
    timing = det.get_timing()
    assert timing["total_ms"] == pytest.approx(
        timing["preprocess_ms"] + timing["inference_ms"] + timing["postprocess_ms"]
    )


def test_recompiled_model_gets_fresh_request(detector):
    det, compiled = detector
    frame = np.zeros((10, 10, 3), np.uint8)
    det.detect(frame)
    replacement = _FakeCompiled()
    det._compiled_model = replacement
    det.detect(frame)
    assert replacement.created == 1


def test_module_exposes_lazy_detector():
    assert isinstance(engine_module.detector, engine_module._LazyOpenVINODetector)
