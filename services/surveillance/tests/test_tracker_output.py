"""The tracker reports only what it saw this frame, inside the frame."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from argus_vision.tracker import MultiObjectTracker

FRAME = np.zeros((432, 768, 3), dtype=np.uint8)
DETECTION = [{"bbox": [10, 10, 50, 100], "confidence": 0.9, "class_label": "person"}]


class _Track:
    def __init__(self, track_id, ltrb, conf=0.9, since_update=0, confirmed=True):
        self.track_id = str(track_id)
        self._ltrb = np.array(ltrb, dtype=float)
        self.det_conf = conf
        self.det_class = "person"
        self.time_since_update = since_update
        self._confirmed = confirmed

    def is_confirmed(self):
        return self._confirmed

    def to_ltrb(self):
        return self._ltrb.copy()


def _update(monkeypatch, tracks) -> list[dict]:
    fake = SimpleNamespace(update_tracks=lambda *_args, **_kwargs: tracks)
    monkeypatch.setattr("argus_vision.tracker._build_deepsort", lambda: fake)
    return MultiObjectTracker().update(DETECTION, FRAME)


def test_a_track_not_matched_this_frame_is_not_reported(monkeypatch):
    coasting = _Track(1, [10, 10, 60, 110], conf=None, since_update=3)
    matched = _Track(2, [100, 10, 150, 110])
    assert [o["object_id"] for o in _update(monkeypatch, [coasting, matched])] == [2]


def test_a_box_overhanging_the_frame_is_clipped(monkeypatch):
    out = _update(monkeypatch, [_Track(1, [-20, -10, 800, 500])])[0]
    assert (out["bbox_x"], out["bbox_y"]) == (0.0, 0.0)
    assert (out["bbox_w"], out["bbox_h"]) == (768.0, 432.0)


def test_a_box_wholly_outside_the_frame_is_dropped(monkeypatch):
    assert _update(monkeypatch, [_Track(1, [1071, 20, 1200, 200])]) == []


def test_an_inverted_box_is_dropped(monkeypatch):
    assert _update(monkeypatch, [_Track(1, [300, 300, 200, 200])]) == []


def test_reported_tracks_always_have_a_confidence_and_a_positive_size(monkeypatch):
    tracks = [
        _Track(1, [10, 10, 60, 110]),
        _Track(2, [-500, 10, -400, 110]),
        _Track(3, [20, 400, 40, 900]),
        _Track(4, [50, 50, 90, 90], since_update=1, conf=None),
    ]
    for obj in _update(monkeypatch, tracks):
        assert obj["confidence"] > 0
        assert obj["bbox_w"] > 0 and obj["bbox_h"] > 0
        assert obj["bbox_x"] + obj["bbox_w"] <= 768
        assert obj["bbox_y"] + obj["bbox_h"] <= 432
