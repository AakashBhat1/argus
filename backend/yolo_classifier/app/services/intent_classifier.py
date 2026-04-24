"""Rule-based intent classifier (Phase 1).

Classifies tracked object trajectories into behavioral intents using
hand-crafted heuristic rules based on trajectory features.

Intent types:
  - passing_through: Linear traversal across the scene
  - loitering: Prolonged stay in a small area
  - surveillance: Slow, deliberate movement with frequent direction changes
  - intrusion: Entry into restricted ROI zone
  - delivery: Approach, brief stop, then departure
  - patrol: Regular, repeating movement pattern
  - unknown: Insufficient signal to classify
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.services.trajectory import TrajectoryFeatures

logger = logging.getLogger(__name__)

CLASSIFIER_VERSION = "rule_v1"


@dataclass
class IntentResult:
    intent_type: str
    confidence: float
    reasoning: str
    features: dict


def classify_intent(features: TrajectoryFeatures) -> IntentResult:
    """Classify a completed trajectory into a behavioral intent."""
    f = features.to_feature_dict()
    duration = features.duration_sec
    avg_speed = features.avg_speed
    max_speed = features.max_speed
    direction_changes = features.direction_changes
    stationary_ratio = features.stationary_ratio
    bbox_coverage = features.bbox_coverage
    had_intrusion = features.had_intrusion
    roi_zone_count = len(features.roi_zones_visited)
    point_count = features.point_count

    # Normalize direction changes by duration for rate
    dir_change_rate = direction_changes / duration if duration > 0 else 0

    # ── Rule 1: Intrusion (highest priority) ─────────────────────────────
    if had_intrusion:
        conf = 0.90
        if duration > 10:
            conf = 0.95
        return IntentResult(
            intent_type="intrusion",
            confidence=conf,
            reasoning=f"Object entered restricted ROI zone(s) {features.roi_zones_visited}. "
                      f"Duration in scene: {duration:.1f}s.",
            features=f,
        )

    # ── Rule 2: Loitering ────────────────────────────────────────────────
    # High stationary ratio + long duration + small area coverage
    if (
        duration >= 15
        and stationary_ratio >= 0.5
        and bbox_coverage < 0.15
    ):
        conf = min(0.90, 0.60 + stationary_ratio * 0.3 + min(duration / 120, 0.1))
        return IntentResult(
            intent_type="loitering",
            confidence=round(conf, 2),
            reasoning=f"Object stayed {duration:.1f}s with {stationary_ratio:.0%} time stationary. "
                      f"Covered only {bbox_coverage:.1%} of scene area.",
            features=f,
        )

    # ── Rule 3: Surveillance / casing ────────────────────────────────────
    # Moderate speed, many direction changes, moderate coverage
    if (
        duration >= 10
        and dir_change_rate >= 0.3
        and avg_speed > STATIONARY_THRESHOLD_SPEED
        and avg_speed < HIGH_SPEED_THRESHOLD
        and bbox_coverage >= 0.05
    ):
        conf = min(0.85, 0.50 + dir_change_rate * 0.15 + min(duration / 60, 0.2))
        return IntentResult(
            intent_type="surveillance",
            confidence=round(conf, 2),
            reasoning=f"Object showed deliberate movement pattern: {direction_changes} direction changes "
                      f"over {duration:.1f}s ({dir_change_rate:.2f}/s). Avg speed: {avg_speed:.1f} px/s.",
            features=f,
        )

    # ── Rule 4: Delivery ─────────────────────────────────────────────────
    # Approaches, stops briefly, then leaves. Entry/exit near edges, stop in middle.
    if (
        5 <= duration <= 60
        and 0.2 <= stationary_ratio <= 0.6
        and bbox_coverage >= 0.08
        and direction_changes >= 1
    ):
        # Check if entry and exit points are on opposite sides or same side
        entry = features.entry_point
        exit_pt = features.exit_point
        entry_exit_dist = (
            ((entry[0] - exit_pt[0]) ** 2 + (entry[1] - exit_pt[1]) ** 2) ** 0.5
            if entry and exit_pt else 0
        )
        # If entry and exit are close, it's a "come and go back" pattern
        if entry_exit_dist < features.total_distance * 0.3 and features.total_distance > 20:
            conf = min(0.80, 0.55 + stationary_ratio * 0.2)
            return IntentResult(
                intent_type="delivery",
                confidence=round(conf, 2),
                reasoning=f"Object approached, paused ({stationary_ratio:.0%} stationary), "
                          f"then returned near entry point. Duration: {duration:.1f}s.",
                features=f,
            )

    # ── Rule 5: Patrol ───────────────────────────────────────────────────
    # Long duration, consistent speed, repeated area coverage
    if (
        duration >= 30
        and direction_changes >= 4
        and 0.1 <= stationary_ratio <= 0.4
        and avg_speed > STATIONARY_THRESHOLD_SPEED
        and bbox_coverage >= 0.15
    ):
        conf = min(0.75, 0.50 + min(duration / 120, 0.15) + min(direction_changes / 20, 0.1))
        return IntentResult(
            intent_type="patrol",
            confidence=round(conf, 2),
            reasoning=f"Object covered {bbox_coverage:.1%} of scene over {duration:.1f}s with "
                      f"{direction_changes} direction changes at steady speed {avg_speed:.1f} px/s.",
            features=f,
        )

    # ── Rule 6: Passing through ──────────────────────────────────────────
    # High coverage, few direction changes, moderate-to-high speed
    if (
        bbox_coverage >= 0.10
        and direction_changes <= 3
        and avg_speed > STATIONARY_THRESHOLD_SPEED
    ):
        conf = min(0.85, 0.60 + bbox_coverage * 0.3)
        return IntentResult(
            intent_type="passing_through",
            confidence=round(conf, 2),
            reasoning=f"Object traversed {bbox_coverage:.1%} of scene with minimal direction changes "
                      f"({direction_changes}). Avg speed: {avg_speed:.1f} px/s over {duration:.1f}s.",
            features=f,
        )

    # ── Fallback: short or ambiguous trajectory ──────────────────────────
    # Try a simpler classification for short tracks
    if duration < 5:
        return IntentResult(
            intent_type="passing_through",
            confidence=0.40,
            reasoning=f"Short trajectory ({duration:.1f}s, {point_count} points). "
                      f"Defaulting to passing_through.",
            features=f,
        )

    return IntentResult(
        intent_type="unknown",
        confidence=0.30,
        reasoning=f"Trajectory features don't match any clear pattern. "
                  f"Duration={duration:.1f}s, speed={avg_speed:.1f}px/s, "
                  f"dir_changes={direction_changes}, coverage={bbox_coverage:.1%}.",
        features=f,
    )


# ── Thresholds ───────────────────────────────────────────────────────────────

STATIONARY_THRESHOLD_SPEED = 2.0   # px/s
HIGH_SPEED_THRESHOLD = 200.0       # px/s
