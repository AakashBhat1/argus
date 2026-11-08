import hashlib
import logging
from pathlib import Path

import numpy as np
import torch
from deep_sort_realtime.deepsort_tracker import DeepSort
from deep_sort_realtime.embedder import embedder_pytorch

from argus_vision.settings import get_settings

logger = logging.getLogger(__name__)

# deep-sort-realtime 1.3.2 loads its bundled MobileNetV2 weights with a bare
# ``torch.load(path)``. That is only safe when torch defaults to
# ``weights_only=True`` (torch >= 2.6). Both conditions are checked before a
# tracker is built, so a swapped weight file or a torch downgrade fails closed
# instead of unpickling arbitrary objects.
EMBEDDER_WEIGHTS_SHA256 = "2f518e773d4402dde55f981ae3078a72ba95c3adccae1d55051a4be844d50197"
MIN_SAFE_TORCH = (2, 6)


class UnsafeEmbedderError(RuntimeError):
    """The appearance embedder cannot be loaded safely."""


def _torch_version() -> tuple[int, int]:
    major, minor = torch.__version__.split("+")[0].split(".")[:2]
    return int(major), int(minor)


def verify_embedder_weights(path: str | Path | None = None) -> None:
    """Refuse to build DeepSort unless its weights and torch are trusted."""
    if _torch_version() < MIN_SAFE_TORCH:
        raise UnsafeEmbedderError(
            f"torch {torch.__version__} loads pickles by default; need >= 2.6"
        )
    weights = Path(path or embedder_pytorch.MOBILENETV2_BOTTLENECK_WTS)
    digest = hashlib.sha256(weights.read_bytes()).hexdigest()
    if digest != EMBEDDER_WEIGHTS_SHA256:
        raise UnsafeEmbedderError(f"unexpected embedder weights SHA-256 {digest} at {weights}")


def _build_deepsort() -> DeepSort:
    verify_embedder_weights()
    settings = get_settings()
    return DeepSort(
        max_age=settings.TRACKER_MAX_AGE,
        n_init=settings.TRACKER_N_INIT,
        max_iou_distance=0.7,
        max_cosine_distance=0.3,
        nn_budget=100,
    )


class MultiObjectTracker:
    """
    DeepSORT-based multi-object tracker.

    Uses Kalman filtering for state prediction and the Hungarian algorithm
    for data association between detections and existing tracks.
    """

    def __init__(self):
        self.tracker = _build_deepsort()
        self._frame_count = 0
        self._track_origins: dict[int, tuple[float, float]] = {}

    def update(self, detections: list[dict], frame: np.ndarray) -> list[dict]:
        """
        Update tracks with new detections.

        Args:
            detections: List of dicts with 'bbox' [x,y,w,h], 'confidence', 'class_label'
            frame: Current video frame (used for re-identification features)

        Returns:
            List of tracked objects with persistent IDs
        """
        self._frame_count += 1

        if not detections:
            tracks = self.tracker.update_tracks([], frame=frame)
            active_ids = {int(track.track_id) for track in tracks}
            self._track_origins = {
                track_id: point
                for track_id, point in self._track_origins.items()
                if track_id in active_ids
            }
            return []

        bbs = []
        for det in detections:
            x, y, w, h = det["bbox"]
            bbs.append(([x, y, w, h], det["confidence"], det["class_label"]))

        tracks = self.tracker.update_tracks(bbs, frame=frame)

        tracked_objects = []
        for track in tracks:
            track_id = int(track.track_id)
            ltrb = track.to_ltrb()
            self._track_origins.setdefault(
                track_id,
                (
                    float((ltrb[0] + ltrb[2]) / 2.0),
                    float(ltrb[3]),
                ),
            )
            if not track.is_confirmed():
                continue

            det_class = track.det_class if hasattr(track, "det_class") else "unknown"
            det_conf = track.det_conf if hasattr(track, "det_conf") else 0.0
            origin_x, origin_y = self._track_origins[track_id]

            tracked_objects.append({
                "object_id": int(track_id),
                "class_label": det_class if det_class else "unknown",
                "confidence": float(det_conf) if det_conf else 0.0,
                "bbox_x": float(ltrb[0]),
                "bbox_y": float(ltrb[1]),
                "bbox_w": float(ltrb[2] - ltrb[0]),
                "bbox_h": float(ltrb[3] - ltrb[1]),
                "frame_number": self._frame_count,
                "track_start_anchor_x": origin_x,
                "track_start_anchor_y": origin_y,
            })

        active_ids = {int(track.track_id) for track in tracks}
        self._track_origins = {
            track_id: point
            for track_id, point in self._track_origins.items()
            if track_id in active_ids
        }

        logger.debug(f"Tracking: {len(tracked_objects)} active tracks (frame {self._frame_count})")
        return tracked_objects

    def reset(self):
        self.tracker = _build_deepsort()
        self._frame_count = 0
        self._track_origins.clear()

    @property
    def frame_count(self) -> int:
        return self._frame_count
