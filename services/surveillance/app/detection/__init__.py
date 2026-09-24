"""Unified detection domain package."""

from argus_vision.engine import COCO_CLASSES, OpenVINODetector, detector
from app.detection.events import ALLOWED_CLASSES, MONITORED_CLASSES, JsonEventLogger, RoiEventReporter
from argus_vision.roi import IntrusionEvent, RoiIntrusionFilter, RoiZone, RoiZoneRepository

__all__ = [
    "COCO_CLASSES",
    "OpenVINODetector",
    "detector",
    "ALLOWED_CLASSES",
    "MONITORED_CLASSES",
    "JsonEventLogger",
    "RoiEventReporter",
    "IntrusionEvent",
    "RoiIntrusionFilter",
    "RoiZone",
    "RoiZoneRepository",
]
