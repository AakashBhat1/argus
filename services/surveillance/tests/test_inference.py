"""Smoke test for inference with real OpenVINO model weights."""

import numpy as np
import pytest

from app.services.detector import detector


@pytest.mark.requires_model
def test_real_model_single_and_batch_inference():
    info = detector.get_model_info()
    assert info["device_actual"] != "unknown"

    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    results = detector.detect(frame)
    assert isinstance(results, list)

    batch_results = detector.detect_batch([frame, frame])
    assert len(batch_results) == 2
    assert all(isinstance(result, list) for result in batch_results)
