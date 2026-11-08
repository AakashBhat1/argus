"""The DeepSORT embedder is only built from pinned weights on a safe torch."""

import pytest

from argus_vision import tracker


def test_bundled_weights_match_the_pin():
    tracker.verify_embedder_weights()


def test_tampered_weights_are_refused(tmp_path):
    fake = tmp_path / "weights.pt"
    fake.write_bytes(b"not the pinned weights")
    with pytest.raises(tracker.UnsafeEmbedderError, match="SHA-256"):
        tracker.verify_embedder_weights(fake)


def test_cameras_share_one_appearance_model(monkeypatch):
    built = []

    class FakeEmbedder:
        def __init__(self):
            built.append(self)

    monkeypatch.setattr(tracker.embedder_pytorch, "MobileNetv2_Embedder", FakeEmbedder)
    monkeypatch.setattr(tracker, "_shared_embedder", None)
    first, second = tracker._build_deepsort(), tracker._build_deepsort()
    assert len(built) == 1
    assert first.embedder is second.embedder


def test_unsafe_weights_stop_the_model_from_loading(monkeypatch):
    def refuse():
        raise tracker.UnsafeEmbedderError("no")

    monkeypatch.setattr(tracker, "verify_embedder_weights", refuse)
    monkeypatch.setattr(tracker, "_shared_embedder", None)
    with pytest.raises(tracker.UnsafeEmbedderError):
        tracker._build_deepsort()


def test_torch_that_defaults_to_pickle_is_refused(monkeypatch):
    monkeypatch.setattr(tracker, "_torch_version", lambda: (2, 5))
    with pytest.raises(tracker.UnsafeEmbedderError, match="torch"):
        tracker.verify_embedder_weights()
