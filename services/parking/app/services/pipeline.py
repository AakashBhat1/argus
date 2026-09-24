"""Per-camera parking pipeline: tracking, gate OCR, bay occupancy, anomalies.

Runs in a worker thread per processed frame (``process``); async side
effects (plate OCR, persistence) are handed back to the owning event loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np

from app.config import get_settings
from app.models import Alert
from app.services.gate_ocr import GATE_ROLES, GateOcrTrigger
from app.services.parking_anomaly import parking_anomaly_detector
from app.services.parking_occupancy import SlotReading, SlotTransition
from app.services.parking_occupancy_service import ParkingOccupancyStage
from app.utils import utc_now
from argus_vision.intent import classify_intent
from argus_vision.tracker import MultiObjectTracker
from argus_vision.trajectory import TrajectoryAccumulator

logger = logging.getLogger(__name__)


@dataclass
class ParkingFrameResult:
    tracked_objects: list[dict]
    slot_readings: list[SlotReading] = field(default_factory=list)
    slot_transitions: list[SlotTransition] = field(default_factory=list)
    anomaly_alerts: list[Alert] = field(default_factory=list)

    def slot_payload(self) -> list[dict]:
        return [reading.to_dict() for reading in self.slot_readings]


class ParkingPipeline:
    def __init__(
        self,
        camera_id: str,
        camera_name: str,
        tenant_id: str,
        role: str,
        gate_roi=None,
    ) -> None:
        settings = get_settings()
        self._camera_id = str(camera_id)
        self._camera_name = camera_name
        self._tenant_id = tenant_id
        self._role = (role or "parking").lower()
        self._tracker = MultiObjectTracker()
        self._trajectory = TrajectoryAccumulator(max_age_seconds=60.0)
        self._gate_ocr = GateOcrTrigger(camera_id=self._camera_id, tenant_id=tenant_id)
        self._gate_ocr.update_meta(self._role, gate_roi)
        self._occupancy = (
            ParkingOccupancyStage(self._camera_id, tenant_id) if self._role == "parking" else None
        )
        self._allowed_classes = {
            str(item).strip().lower() for item in settings.ALLOWED_CLASSES if str(item).strip()
        }

    @property
    def role(self) -> str:
        return self._role

    def bind_loop(self, loop) -> None:
        self._gate_ocr.bind_loop(loop)

    def update_gate(self, role: Optional[str], gate_roi) -> None:
        self._role = (role or self._role).lower()
        self._gate_ocr.update_meta(self._role, gate_roi)
        if self._role == "parking" and self._occupancy is None:
            self._occupancy = ParkingOccupancyStage(self._camera_id, self._tenant_id)
        elif self._role != "parking":
            self._occupancy = None

    def gate_ocr_status(self) -> dict:
        return self._gate_ocr.status()

    def slots(self):
        return self._occupancy.slots() if self._occupancy is not None else []

    def process(
        self,
        detections: list[dict],
        frame: np.ndarray,
        timestamp: Optional[datetime] = None,
    ) -> ParkingFrameResult:
        if self._allowed_classes:
            detections = [
                det
                for det in detections
                if str(det.get("class_label", "")).strip().lower() in self._allowed_classes
            ]
        tracked = self._tracker.update(detections, frame)
        event_time = timestamp or utc_now()

        alerts: list[Alert] = []
        if self._occupancy is not None:
            for features in self._trajectory.update(tracked):
                intent = classify_intent(features)
                alerts.extend(
                    parking_anomaly_detector.detect_lane_loitering(
                        camera_id=self._camera_id,
                        tenant_id=self._tenant_id,
                        features=features,
                        intent_type=intent.intent_type,
                        now=event_time,
                    )
                )
                alerts.extend(
                    parking_anomaly_detector.detect_car_hopping(
                        camera_id=self._camera_id,
                        tenant_id=self._tenant_id,
                        features=features,
                        slots=self._occupancy.slots(),
                        frame_shape=frame.shape,
                        now=event_time,
                    )
                )

        if self._role in GATE_ROLES:
            self._gate_ocr.process_frame(tracked, frame)

        readings: list[SlotReading] = []
        transitions: list[SlotTransition] = []
        if self._occupancy is not None:
            tick = self._occupancy.process(frame, tracked)
            readings, transitions = tick.readings, tick.transitions

        return ParkingFrameResult(tracked, readings, transitions, alerts)

    def reset(self) -> None:
        self._tracker.reset()
        self._trajectory.reset()
        self._gate_ocr.reset()
        if self._occupancy is not None:
            self._occupancy.reset()
