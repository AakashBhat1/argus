"""Unit tests: ParkBot command whitelist + arg validation."""

from __future__ import annotations

import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-that-is-at-least-32-characters-long!")
os.environ.setdefault("DEBUG", "true")

from app.services.parking_commands import ALLOWED_ACTIONS, validate_command


def test_whitelist_contains_expected_actions():
    assert "release_space" in ALLOWED_ACTIONS
    assert "assign_space" in ALLOWED_ACTIONS


def test_reject_unknown_action():
    ok, msg, payload = validate_command({"action": "drop_table", "space_id": "G-01"})
    assert ok is False
    assert payload is None
    assert "whitelist" in msg.lower() or "not in" in msg.lower()


def test_reject_missing_action():
    ok, msg, _ = validate_command({"space_id": "G-01"})
    assert ok is False
    assert "action" in msg.lower()


def test_release_space_valid():
    ok, msg, payload = validate_command({"action": "release_space", "space_id": "A-01"})
    assert ok is True
    assert msg == "ok"
    assert payload == {"action": "release_space", "space_id": "A-01"}


def test_release_space_missing_space_id():
    ok, msg, payload = validate_command({"action": "release_space"})
    assert ok is False
    assert payload is None


def test_assign_space_valid_plate():
    ok, msg, payload = validate_command(
        {"action": "assign_space", "plate_text": "MH12AB1234"}
    )
    assert ok is True
    assert payload["action"] == "assign_space"
    assert payload["plate_text"] == "MH12AB1234"


def test_assign_space_invalid_plate():
    ok, msg, payload = validate_command(
        {"action": "park_vehicle", "plate_text": "NOT-A-PLATE"}
    )
    assert ok is False
    assert payload is None
    assert "plate" in msg.lower() or "Invalid" in msg


def test_non_dict_payload_rejected():
    ok, msg, payload = validate_command("release_space")  # type: ignore[arg-type]
    assert ok is False
    assert payload is None
