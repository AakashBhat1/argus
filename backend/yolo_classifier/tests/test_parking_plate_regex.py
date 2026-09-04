"""Unit tests: Indian plate regex + normalization."""

from __future__ import annotations

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters-long!")
os.environ.setdefault("DEBUG", "true")

import pytest

from app.services.ocr_service import is_valid_plate, normalize_plate_text
from app.services.parking_commands import validate_plate


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MH12AB1234", "MH12AB1234"),
        ("mh12ab1234", "MH12AB1234"),
        ("MH 12 AB 1234", "MH12AB1234"),
        ("DL01CA0001", "DL01CA0001"),
        ("KA03X1234", "KA03X1234"),
    ],
)
def test_normalize_plate(raw, expected):
    assert normalize_plate_text(raw) == expected


@pytest.mark.parametrize(
    "plate,ok",
    [
        ("MH12AB1234", True),
        ("DL01C0001", True),  # single letter series
        ("KA03XX1234", True),
        ("MH12AB123", False),  # too short
        ("M12AB1234", False),
        ("12MHAB1234", False),
        ("", False),
        ("INVALID", False),
    ],
)
def test_is_valid_plate(plate, ok):
    assert is_valid_plate(plate) is ok


def test_validate_plate_helper():
    ok, val = validate_plate("mh12ab1234")
    assert ok is True
    assert val == "MH12AB1234"

    ok, err = validate_plate("BAD")
    assert ok is False
    assert "Invalid" in err


@pytest.mark.parametrize(
    "text, ok",
    [
        ("KA01AB1234", True),   # strict Indian format
        ("22BH1234AB", True),   # BH series: loose rule (letters + digits, >= 6)
        ("L", False),           # OCR noise
        ("1234", False),        # digits only
        ("ABCDEF", False),      # letters only
        ("ABCDEFGHIJKLM1", False),  # too long
    ],
)
def test_is_plausible_plate_rejects_ocr_noise(text, ok):
    from app.services.ocr_service import is_plausible_plate

    assert is_plausible_plate(text) is ok
