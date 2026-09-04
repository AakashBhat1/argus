"""Regression tests for detector loading at import and application startup."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor


BACKEND_DIR = Path(__file__).resolve().parents[1]
MISSING_MODEL = BACKEND_DIR / "models" / "ci-definitely-missing.xml"


def _run_in_clean_interpreter(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["OPENVINO_MODEL_PATH"] = str(MISSING_MODEL)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_detection_package_import_does_not_load_missing_model():
    result = _run_in_clean_interpreter("import app.detection")

    assert result.returncode == 0, result.stderr


def test_application_startup_still_fails_loudly_for_missing_model():
    result = _run_in_clean_interpreter(
        """
import asyncio

from app.main import app, lifespan


async def check_startup():
    try:
        async with lifespan(app):
            pass
    except FileNotFoundError as exc:
        message = str(exc)
        assert "OpenVINO model not found:" in message
        assert "Run model conversion before startup." in message
        return
    raise AssertionError("startup unexpectedly succeeded without model weights")


asyncio.run(check_startup())
"""
    )

    assert result.returncode == 0, result.stderr


def test_lazy_detector_initializes_once_under_concurrent_first_access(monkeypatch):
    from app.detection import engine

    instances: list[object] = []

    def construct_detector():
        time.sleep(0.02)
        instance = object()
        instances.append(instance)
        return instance

    monkeypatch.setattr(engine, "OpenVINODetector", construct_detector)
    lazy_detector = engine._LazyOpenVINODetector()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: lazy_detector.initialize(), range(8)))

    assert len(instances) == 1
    assert all(result is instances[0] for result in results)
