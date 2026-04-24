import logging
import numpy as np
from deep_sort_realtime.deepsort_tracker import DeepSort
from app.config import get_settings

logger = logging.getLogger(__name__)


class MultiObjectTracker:
    """
    DeepSORT-based multi-object tracker.

    Uses Kalman filtering for state prediction and the Hungarian algorithm
    for data association between detections and existing tracks.
    """

    def __init__(self):
        settings = get_settings()
        self.tracker = DeepSort(
            
            max_age=settings.TRACKER_MAX_AGE,
            n_init=settings.TRACKER_N_INIT,
            max_iou_distance=0.7,
            max_cosine_distance=0.3,
            nn_budget=100,
        )
        self._frame_count = 0

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
            self.tracker.update_tracks([], frame=frame)
            return []

        bbs = []
        for det in detections:
            x, y, w, h = det["bbox"]
            bbs.append(([x, y, w, h], det["confidence"], det["class_label"]))

        tracks = self.tracker.update_tracks(bbs, frame=frame)

        tracked_objects = []
        for track in tracks:
            if not track.is_confirmed():
                continue

            track_id = track.track_id
            ltrb = track.to_ltrb()

            det_class = track.det_class if hasattr(track, "det_class") else "unknown"
            det_conf = track.det_conf if hasattr(track, "det_conf") else 0.0

            tracked_objects.append({
                "object_id": int(track_id),
                "class_label": det_class if det_class else "unknown",
                "confidence": float(det_conf) if det_conf else 0.0,
                "bbox_x": float(ltrb[0]),
                "bbox_y": float(ltrb[1]),
                "bbox_w": float(ltrb[2] - ltrb[0]),
                "bbox_h": float(ltrb[3] - ltrb[1]),
                "frame_number": self._frame_count,
            })

        logger.debug(f"Tracking: {len(tracked_objects)} active tracks (frame {self._frame_count})")
        return tracked_objects

    def reset(self):
        settings = get_settings()
        self.tracker = DeepSort(
           
            max_age=settings.TRACKER_MAX_AGE,
            n_init=settings.TRACKER_N_INIT,
            max_iou_distance=0.7,
            max_cosine_distance=0.3,
            nn_budget=100,
        )
        self._frame_count = 0

    @property
    def frame_count(self) -> int:
        return self._frame_count
