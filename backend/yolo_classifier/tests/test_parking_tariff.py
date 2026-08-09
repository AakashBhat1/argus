"""Unit tests: parking tariff calculation."""

from __future__ import annotations

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters-long!")
os.environ.setdefault("DEBUG", "true")

import pytest

from app.config import get_settings
from app.services.parking_service import calculate_tariff


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_short_stay_under_free_minutes():
    settings = get_settings()
    free = settings.PARKING_FREE_MINUTES
    short = settings.PARKING_SHORT_STAY_RATE
    assert calculate_tariff(0) == short
    assert calculate_tariff(free) == short
    assert calculate_tariff(max(0, free - 1)) == short


def test_hourly_billing_rounds_up():
    settings = get_settings()
    rate = settings.PARKING_RATE_PER_HOUR
    free = settings.PARKING_FREE_MINUTES
    # Just over free window → 1 hour
    assert calculate_tariff(free + 1) == rate
    # 61 minutes → 2 hours
    assert calculate_tariff(61) == 2 * rate
    # Exactly 120 minutes → 2 hours
    assert calculate_tariff(120) == 2 * rate
    # 121 minutes → 3 hours
    assert calculate_tariff(121) == 3 * rate


def test_negative_duration_treated_as_zero():
    settings = get_settings()
    assert calculate_tariff(-5) == settings.PARKING_SHORT_STAY_RATE
