"""Unified pipeline: tracking + ROI filtering + intent classification."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np

from app.config import get_settings
from app.detection.events import RoiEventReporter
from argus_vision.geometry import CameraCalibration, CameraGeometry
from argus_vision.roi import IntrusionEvent, RoiIntrusionFilter
from app.services.authorization import authorization_registry
from argus_vision.intent import IntentResult, classify_intent
from app.services.intent_persistence import save_track_and_intent
from app.services.risk_engine import RiskEngine, RiskEvent
from argus_vision.tracker import MultiObjectTracker
from argus_vision.trajectory import TrajectoryAccumulator, TrajectoryFeatures
from app.utils import utc_now

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    tracked_objects: list[dict]
    intrusion_events: list[IntrusionEvent]
    intent_events: list[IntentResult] = field(default_factory=list)
    risk_events: list[RiskEvent] = field(default_factory=list)
    zones: list[dict] = field(default_factory=list)
    risk_summary: dict = field(default_factory=dict)
    arm_mode: str = "auto"

    def intrusion_payload(self) -> list[dict]:
        return [event.to_dict() for event in self.intrusion_events]

    def risk_payload(self) -> list[dict]:
        return [event.to_dict() for event in self.risk_events]


def _parse_resolution(value: Optional[str]) -> tuple[int, int]:
    try:
        width, height = str(value or "1280x720").lower().split("x")
        return max(1, int(width)), max(1, int(height))
    except (ValueError, AttributeError):
        return 1280, 720


class IntrusionPipeline:
    """Runs post-inference processing for a single camera stream."""

    def __init__(
        self,
        camera_id: str,
        camera_name: str,
        tenant_id: str = '1',
        camera_role: str = 'surveillance',
        calibration: Optional[dict] = None,
        resolution: Optional[str] = None,
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
        width, height = _parse_resolution(resolution)
        cal = CameraCalibration.from_dict(calibration) if calibration else CameraCalibration(
            hfov_deg=float(settings.CAMERA_DEFAULT_HFOV_DEG)
        )
        self._geometry = CameraGeometry(width, height, cal)
        self._risk = RiskEngine(
            camera_id=self._camera_id,
            tenant_id=self._tenant_id,
            geometry=self._geometry,
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

    @property
    def risk_engine(self) -> RiskEngine:
        return self._risk

    @property
    def geometry(self) -> CameraGeometry:
        return self._geometry

    def bind_loop(self, loop) -> None:
        """Kept for API symmetry; the surveillance pipeline has no async stages."""

    def update_calibration(self, calibration: Optional[dict]) -> None:
        """Hot-swap camera calibration (called when the camera record changes)."""
        cal = CameraCalibration.from_dict(calibration) if calibration else CameraCalibration(
            hfov_deg=float(get_settings().CAMERA_DEFAULT_HFOV_DEG)
        )
        self._geometry = CameraGeometry(self._geometry.width, self._geometry.height, cal)
        self._risk.set_geometry(self._geometry)

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
        arm_mode = authorization_registry.arm_mode(self._tenant_id)
        intrusion_events = self._roi_filter.evaluate(
            tracked_objects=tracked,
            frame_shape=frame.shape,
            timestamp=ts_unix,
            now_dt=event_time,
            arm_mode=arm_mode,
        )
        risk_events = self._risk.evaluate(
            tracked_objects=tracked,
            zones=self._roi_filter.zones(),
            intrusion_events=intrusion_events,
            frame_shape=frame.shape,
            now=ts_unix,
            now_dt=event_time,
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
        for features in self._trajectory.update(tracked):
            intent = classify_intent(features)
            intent_events.append(intent)
            save_track_and_intent(
                camera_id=self._camera_id,
                features=features,
                intent=intent,
                tenant_id=self._tenant_id,
            )

        return PipelineResult(
            tracked_objects=tracked,
            intrusion_events=intrusion_events,
            intent_events=intent_events,
            risk_events=risk_events,
            zones=self._roi_filter.zones_payload(event_time, arm_mode),
            risk_summary=self._risk.summary(),
            arm_mode=arm_mode,
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
        self._risk.reset()
        self._reporter.reset(camera_id=self._camera_id)
        self._trajectory.reset()
