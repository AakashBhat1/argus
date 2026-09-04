"""Intent classifier: zone violations no longer short-circuit behaviour."""

from __future__ import annotations

from app.services.intent_classifier import classify_intent
from app.services.trajectory import TrajectoryFeatures


def _features(**overrides) -> TrajectoryFeatures:
    base = dict(
        object_id=1,
        class_label="person",
        duration_sec=8.0,
        total_distance=800.0,
        avg_speed=100.0,
        max_speed=140.0,
        direction_changes=1,
        stationary_ratio=0.0,
        bbox_coverage=0.6,
        entry_point=[0.0, 300.0],
        exit_point=[900.0, 300.0],
        roi_zones_visited=[1],
        had_intrusion=True,
        trajectory_points=[],
        point_count=40,
    )
    base.update(overrides)
    return TrajectoryFeatures(**base)


def test_crossing_zone_while_moving_is_passing_through():
    result = classify_intent(_features())
    assert result.intent_type == "passing_through"
    assert "Crossed armed zone" in result.reasoning


def test_lingering_inside_zone_is_intrusion():
    result = classify_intent(
        _features(duration_sec=30.0, total_distance=40.0, avg_speed=1.3, stationary_ratio=0.8, bbox_coverage=0.02)
    )
    assert result.intent_type == "intrusion"
    assert result.confidence >= 0.9
    assert "loitering" in result.reasoning


def test_no_zone_visit_keeps_behaviour_only():
    result = classify_intent(_features(had_intrusion=False, roi_zones_visited=[]))
    assert result.intent_type == "passing_through"
    assert "zone" not in result.reasoning.lower()
