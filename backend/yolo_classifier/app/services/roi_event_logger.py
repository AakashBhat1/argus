"""Backward-compatible ROI event logger exports.

ROI event logging is now implemented in ``app.detection.events``.
"""

from app.detection.events import ALLOWED_CLASSES, MONITORED_CLASSES, JsonEventLogger, RoiEventReporter

__all__ = ["ALLOWED_CLASSES", "MONITORED_CLASSES", "JsonEventLogger", "RoiEventReporter"]
