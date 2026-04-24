"""Backward-compatible detector exports.

The detection engine now lives in ``app.detection.engine``.
"""

from app.detection.engine import COCO_CLASSES, OpenVINODetector, detector

__all__ = ["COCO_CLASSES", "OpenVINODetector", "detector"]
