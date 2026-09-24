"""Backward-compatible detector exports.

The detection engine now lives in ``argus_vision.engine``.
"""

from argus_vision.engine import COCO_CLASSES, OpenVINODetector, detector

__all__ = ["COCO_CLASSES", "OpenVINODetector", "detector"]
