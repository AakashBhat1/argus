"""Unified pipeline: tracking + ROI filtering + intent classification."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np

from app.config import get_settings
from app.detection.events import RoiEventReporter
from app.detection.roi import IntrusionEvent, RoiIntrusionFilter
from app.services.intent_classifier import IntentResult, classify_intent
from app.services.intent_persistence import save_track_and_intent
from app.services.tracker import MultiObjectTracker
from app.services.trajectory import TrajectoryAccumulator, TrajectoryFeatures
from app.utils import utc_now

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    tracked_objects: list[dict]
    intrusion_events: list[IntrusionEvent]
    intent_events: list[IntentResult] = field(default_factory=list)

    def intrusion_payload(self) -> list[dict]:
        return [event.to_dict() for event in self.intrusion_events]


class IntrusionPipeline:
    """Runs post-inference processing for a single camera stream."""

    def __init__(self, camera_id: str, camera_name: str):
        settings = get_settings()
        self._camera_id = str(camera_id)
        self._camera_name = camera_name
        self._tracker = MultiObjectTracker()
        self._roi_filter = RoiIntrusionFilter(camera_id=self._camera_id)
        self._reporter = RoiEventReporter()
        self._trajectory = TrajectoryAccumulator(max_age_seconds=60.0)
        self._allowed_classes = {
            str(item).strip().lower()
            for item in settings.ALLOWED_CLASSES
            if str(item).strip()
        }

    @property
    def camera_id(self) -> str:
        return self._camera_id

    @property
    def camera_name(self) -> str:
        return self._camera_name

    def process(
        self,
        detections: list[dict],
        frame: np.ndarray,
        timestamp: Optional[datetime] = None,
    ) -> PipelineResult:
        if self._allowed_classes:
            filtered_detections = [
                det
                for det in detections
                if str(det.get("class_label", "")).strip().lower() in self._allowed_classes
            ]
        else:
            filtered_detections = list(detections)
        tracked = self._tracker.update(filtered_detections, frame)
        event_time = timestamp or utc_now()
        ts_unix = event_time.timestamp()
        intrusion_events = self._roi_filter.evaluate(
            tracked_objects=tracked,
            frame_shape=frame.shape,
            timestamp=ts_unix,
        )
        self._reporter.record_detections(
            camera_id=self._camera_id,
            tracked_objects=tracked,
            intrusion_events=intrusion_events,
            timestamp=event_time,
        )

        # Trajectory accumulation + intent classification
        intent_events: list[IntentResult] = []
        ended_tracks = self._trajectory.update(tracked)
        for features in ended_tracks:
            intent = classify_intent(features)
            intent_events.append(intent)
            save_track_and_intent(
                camera_id=self._camera_id,
                features=features,
                intent=intent,
            )

        return PipelineResult(
            tracked_objects=tracked,
            intrusion_events=intrusion_events,
            intent_events=intent_events,
        )

    def reset(self) -> None:
        # Flush remaining trajectories before reset
        remaining = self._trajectory.flush_all()
        for features in remaining:
            intent = classify_intent(features)
            save_track_and_intent(
                camera_id=self._camera_id,
                features=features,
                intent=intent,
            )
        self._tracker.reset()
        self._roi_filter.reset()
        self._reporter.reset(camera_id=self._camera_id)
        self._trajectory.reset()
