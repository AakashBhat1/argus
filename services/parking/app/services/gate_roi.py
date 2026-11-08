"""Parse and validate a gate camera's trigger polygon."""

from __future__ import annotations

import math
from typing import Any, Optional

MAX_GATE_ROI_POINTS = 64
MIN_POINTS = 3
# Smallest polygon that can contain anything, in normalized and pixel units.
_MIN_AREA_NORMALIZED = 1e-5
_MIN_AREA_PIXELS = 1.0


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not finite")
    return number


def parse_gate_roi(gate_roi: Any) -> Optional[list[list[float]]]:
    """Return the polygon's points, or None if ``gate_roi`` is not a polygon.

    Accepted shapes: ``[[x, y], ...]``, ``[{"x": x, "y": y}, ...]``, a flat
    ``[x1, y1, x2, y2, ...]`` and ``{"points": <any of those>}``. Coordinates
    are normalized when all are at most 1, and pixels otherwise.
    """
    if isinstance(gate_roi, dict):
        gate_roi = gate_roi.get("points")
    if not isinstance(gate_roi, (list, tuple)) or not gate_roi:
        return None
    try:
        if isinstance(gate_roi[0], (int, float)):
            if len(gate_roi) % 2:
                return None
            pairs = [(gate_roi[i], gate_roi[i + 1]) for i in range(0, len(gate_roi), 2)]
        else:
            pairs = [(p["x"], p["y"]) if isinstance(p, dict) else (p[0], p[1]) for p in gate_roi]
        points = [[_number(x), _number(y)] for x, y in pairs]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return points if len(points) >= MIN_POINTS else None


def _area(points: list[list[float]]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def gate_roi_problem(gate_roi: Any) -> Optional[str]:
    """Explain why this value cannot be saved as a gate polygon, or None."""
    points = parse_gate_roi(gate_roi)
    if points is None:
        return f"gate_roi must be a polygon of at least {MIN_POINTS} numeric points"
    if len(points) > MAX_GATE_ROI_POINTS:
        return f"gate_roi may have at most {MAX_GATE_ROI_POINTS} points"
    coordinates = [value for point in points for value in point]
    if min(coordinates) < 0:
        return "gate_roi coordinates must not be negative"
    minimum = _MIN_AREA_NORMALIZED if max(coordinates) <= 1.0 else _MIN_AREA_PIXELS
    if _area(points) < minimum:
        return "gate_roi must enclose an area; these points lie on one line"
    return None
