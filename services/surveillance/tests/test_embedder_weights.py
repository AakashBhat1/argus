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


def test_torch_that_defaults_to_pickle_is_refused(monkeypatch):
    monkeypatch.setattr(tracker, "_torch_version", lambda: (2, 5))
    with pytest.raises(tracker.UnsafeEmbedderError, match="torch"):
        tracker.verify_embedder_weights()
