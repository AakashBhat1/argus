"""ParkBot command whitelist + argument validation (pure, no I/O).

LLM proposes a structured action; this module validates before execution.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.services.ocr_service import PLATE_REGEX, is_valid_plate, normalize_plate_text

# Whitelist of executable actions
ALLOWED_ACTIONS = frozenset({
    "release_space",
    "assign_space",
    "park_vehicle",  # alias of assign with optional plate
})

# Human space codes: A-01, G-01, F1-04, F2-08, etc.
SPACE_ID_REGEX = re.compile(r"^[A-Z]{1,2}\d?-\d{2}$|^[GF]\d?-\d{2}$|^F[12]-\d{2}$", re.IGNORECASE)


def validate_plate(plate: str) -> tuple[bool, str]:
    """Return (ok, normalized_or_error)."""
    if not plate or not str(plate).strip():
        return False, "plate_text is required"
    normalized = normalize_plate_text(str(plate))
    if not is_valid_plate(normalized):
        return False, (
            f"Invalid plate format '{plate}'. "
            f"Expected pattern {PLATE_REGEX.pattern}"
        )
    return True, normalized


def validate_space_id(space_id: str) -> tuple[bool, str]:
    if not space_id or not str(space_id).strip():
        return False, "space_id is required"
    code = str(space_id).strip().upper()
    if not SPACE_ID_REGEX.match(code) and len(code) > 36:
        # Allow UUID surrogate ids (36 chars with hyphens)
        return False, f"Invalid space_id '{space_id}'"
    # UUID form
    if re.match(r"^[0-9a-fA-F-]{36}$", code):
        return True, code
    if not SPACE_ID_REGEX.match(code):
        # Still accept short alphanumeric space codes used by seeder
        if re.match(r"^[A-Z0-9-]{2,12}$", code):
            return True, code
        return False, f"Invalid space_id '{space_id}'"
    return True, code


def validate_command(action_payload: dict[str, Any]) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """Validate structured ParkBot action.

    Returns:
        (ok, message, normalized_payload_or_None)
    """
    if not isinstance(action_payload, dict):
        return False, "Action payload must be a JSON object", None

    action = str(action_payload.get("action", "")).strip().lower()
    if not action:
        return False, "Missing 'action' field", None
    if action not in ALLOWED_ACTIONS:
        return False, f"Action '{action}' is not in the whitelist", None

    if action == "release_space":
        ok, space_or_err = validate_space_id(str(action_payload.get("space_id", "")))
        if not ok:
            return False, space_or_err, None
        return True, "ok", {"action": "release_space", "space_id": space_or_err}

    if action in ("assign_space", "park_vehicle"):
        ok, plate_or_err = validate_plate(str(action_payload.get("plate_text", "")))
        if not ok:
            return False, plate_or_err, None
        normalized = {"action": "assign_space", "plate_text": plate_or_err}
        if action_payload.get("space_id"):
            sok, space_or_err = validate_space_id(str(action_payload["space_id"]))
            if not sok:
                return False, space_or_err, None
            normalized["space_id"] = space_or_err
        return True, "ok", normalized

    return False, f"Unhandled action '{action}'", None
