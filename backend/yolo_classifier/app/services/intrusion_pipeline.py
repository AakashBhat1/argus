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
from app.models import Alert
from app.services.gate_ocr import GateOcrTrigger
from app.services.intent_classifier import IntentResult, classify_intent
from app.services.intent_persistence import save_track_and_intent
from app.services.parking_anomaly import parking_anomaly_detector
from app.services.parking_occupancy import SlotReading, SlotTransition
from app.services.parking_occupancy_service import ParkingOccupancyStage
from app.services.tracker import MultiObjectTracker
from app.services.trajectory import TrajectoryAccumulator, TrajectoryFeatures
from app.utils import utc_now

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    tracked_objects: list[dict]
    intrusion_events: list[IntrusionEvent]
    intent_events: list[IntentResult] = field(default_factory=list)
    slot_readings: list[SlotReading] = field(default_factory=list)
    slot_transitions: list[SlotTransition] = field(default_factory=list)
    parking_anomaly_alerts: list[Alert] = field(default_factory=list)

    def intrusion_payload(self) -> list[dict]:
        return [event.to_dict() for event in self.intrusion_events]

    def slot_payload(self) -> list[dict]:
        return [reading.to_dict() for reading in self.slot_readings]


class IntrusionPipeline:
    """Runs post-inference processing for a single camera stream."""

    def __init__(
        self,
        camera_id: str,
        camera_name: str,
        tenant_id: str = '1',
        camera_role: str = 'surveillance',
    ):
        settings = get_settings()
        self._camera_id = str(camera_id)
        self._camera_name = camera_name
        self._tenant_id = tenant_id
        self._camera_role = camera_role
        self._tracker = MultiObjectTracker()
        self._roi_filter = RoiIntrusionFilter(camera_id=self._camera_id)
        self._reporter = RoiEventReporter()
        self._trajectory = TrajectoryAccumulator(max_age_seconds=60.0)
        self._gate_ocr = GateOcrTrigger(camera_id=self._camera_id, tenant_id=tenant_id)
        self._parking_occupancy = (
            ParkingOccupancyStage(self._camera_id, tenant_id)
            if camera_role == 'parking'
            else None
        )
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
            tenant_id=self._tenant_id,
        )

        # Trajectory accumulation + intent classification
        intent_events: list[IntentResult] = []
        parking_anomaly_alerts: list[Alert] = []
        ended_tracks = self._trajectory.update(tracked)
        for features in ended_tracks:
            intent = classify_intent(features)
            intent_events.append(intent)
            save_track_and_intent(
                camera_id=self._camera_id,
                features=features,
                intent=intent,
                tenant_id=self._tenant_id,
            )
            if self._parking_occupancy is not None:
                parking_anomaly_alerts.extend(
                    parking_anomaly_detector.detect_lane_loitering(
                        camera_id=self._camera_id,
                        tenant_id=self._tenant_id,
                        features=features,
                        intent_type=intent.intent_type,
                        now=event_time,
                    )
                )
                parking_anomaly_alerts.extend(
                    parking_anomaly_detector.detect_car_hopping(
                        camera_id=self._camera_id,
                        tenant_id=self._tenant_id,
                        features=features,
                        slots=self._parking_occupancy.slots(),
                        frame_shape=frame.shape,
                        now=event_time,
                    )
                )

        # Smart parking: gate ROI collision → OCR once per track_id
        self._gate_ocr.process_frame(tracked, frame)
        slot_readings: list[SlotReading] = []
        slot_transitions: list[SlotTransition] = []
        if self._parking_occupancy is not None:
            tick = self._parking_occupancy.process(frame, tracked)
            slot_readings = tick.readings
            slot_transitions = tick.transitions

        return PipelineResult(
            tracked_objects=tracked,
            intrusion_events=intrusion_events,
            intent_events=intent_events,
            slot_readings=slot_readings,
            slot_transitions=slot_transitions,
            parking_anomaly_alerts=parking_anomaly_alerts,
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
                tenant_id=self._tenant_id,
            )
        self._tracker.reset()
        self._roi_filter.reset()
        self._reporter.reset(camera_id=self._camera_id)
        self._trajectory.reset()
        self._gate_ocr.reset()
        if self._parking_occupancy is not None:
            self._parking_occupancy.reset()
