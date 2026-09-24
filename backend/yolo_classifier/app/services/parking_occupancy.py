'''Pure parking-space occupancy scoring and debounce state.'''

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from app.detection.roi import RoiZone

WARP_WIDTH = 64
WARP_HEIGHT = 128


@dataclass(frozen=True)
class SlotGeometry:
    space_id: str
    db_id: str
    polygon: np.ndarray

    def __post_init__(self) -> None:
        points = np.asarray(self.polygon, dtype=np.float32)
        if points.shape != (4, 2):
            raise ValueError('slot polygon must contain exactly four [x, y] points')
        if np.any(points < 0.0) or np.any(points > 1.0):
            raise ValueError('slot polygon coordinates must be normalized to [0, 1]')
        object.__setattr__(self, 'polygon', points.copy())


@dataclass(frozen=True)
class SlotReading:
    space_id: str
    occupied: bool
    score: float
    source: str
    db_id: str = ''

    def to_dict(self) -> dict:
        return {
            'space_id': self.space_id,
            'occupied': self.occupied,
            'score': round(self.score, 4),
            'source': self.source,
        }


@dataclass(frozen=True)
class SlotTransition:
    space_id: str
    db_id: str
    occupied: bool
    score: float
    source: str

    def to_dict(self) -> dict:
        return {
            'space_id': self.space_id,
            'occupied': self.occupied,
            'score': round(self.score, 4),
            'source': self.source,
        }


DEFAULT_VEHICLE_CLASSES = ('car', 'truck', 'bus', 'motorcycle')
# Vehicles that usually span more than one bay: judged by how much of the
# bay their ground footprint covers, since IoU against a single bay is small.
LARGE_VEHICLE_CLASSES = frozenset({'truck', 'bus'})
# Vehicles much smaller than a bay: judged by how much of the vehicle lies
# inside the bay.
SMALL_VEHICLE_CLASSES = frozenset({'motorcycle', 'bicycle'})
# Fraction of the bbox (from the bottom) used as a ground-footprint proxy.
FOOTPRINT_FRACTION = 0.5


class OccupancyDetector:
    def __init__(
        self,
        *,
        hi: float = 0.22,
        lo: float = 0.10,
        iou_min: float = 0.40,
        texture_fallback: bool = False,
        vehicle_classes: tuple[str, ...] | list[str] = DEFAULT_VEHICLE_CLASSES,
    ):
        if not 0.0 <= lo < hi <= 1.0:
            raise ValueError('occupancy thresholds must satisfy 0 <= lo < hi <= 1')
        if not 0.0 <= iou_min <= 1.0:
            raise ValueError('iou_min must be within [0, 1]')
        self._hi = float(hi)
        self._lo = float(lo)
        self._iou_min = float(iou_min)
        self._texture_fallback = bool(texture_fallback)
        self._vehicle_classes = frozenset(
            str(label).strip().lower() for label in vehicle_classes if str(label).strip()
        )

    @staticmethod
    def _binary_mask(frame: np.ndarray) -> np.ndarray:
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError('frame must be a BGR image')
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        thresholded = cv2.adaptiveThreshold(
            blurred,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            25,
            16,
        )
        median = cv2.medianBlur(thresholded, 5)
        kernel = np.ones((3, 3), dtype=np.uint8)
        return cv2.dilate(median, kernel, iterations=1)

    @staticmethod
    def _pixel_polygon(slot: SlotGeometry, width: int, height: int) -> np.ndarray:
        zone = RoiZone(
            zone_id=0,
            name=slot.space_id,
            normalized_points=slot.polygon,
            threshold_sec=0.0,
            color=(0, 0, 0),
        )
        return zone.to_pixel_points(width, height)

    @staticmethod
    def _box(detection: dict, footprint: bool = False) -> np.ndarray | None:
        x = float(detection.get('bbox_x', 0.0))
        y = float(detection.get('bbox_y', 0.0))
        width = max(0.0, float(detection.get('bbox_w', 0.0)))
        height = max(0.0, float(detection.get('bbox_h', 0.0)))
        if width == 0.0 or height == 0.0:
            return None
        if footprint:
            y += height * (1.0 - FOOTPRINT_FRACTION)
            height *= FOOTPRINT_FRACTION
        return np.array(
            [[x, y], [x + width, y], [x + width, y + height], [x, y + height]],
            dtype=np.float32,
        )

    @staticmethod
    def _intersection(polygon: np.ndarray, rectangle: np.ndarray) -> float:
        intersection, _ = cv2.intersectConvexConvex(polygon.astype(np.float32), rectangle)
        return float(intersection)

    @classmethod
    def _detection_iou(cls, polygon: np.ndarray, detection: dict) -> float:
        rectangle = cls._box(detection)
        if rectangle is None:
            return 0.0
        intersection = cls._intersection(polygon, rectangle)
        box_area = abs(cv2.contourArea(rectangle))
        union = abs(cv2.contourArea(polygon.astype(np.float32))) + box_area - intersection
        return intersection / union if union > 0.0 else 0.0

    def _detection_score(self, polygon: np.ndarray, detection: dict) -> float:
        """Occupancy evidence in [0, 1] that ``detection`` occupies the bay."""
        label = str(detection.get('class_label', '')).strip().lower()
        if label not in self._vehicle_classes:
            return 0.0
        iou = self._detection_iou(polygon, detection)
        if label in LARGE_VEHICLE_CLASSES:
            footprint = self._box(detection, footprint=True)
            slot_area = abs(cv2.contourArea(polygon.astype(np.float32)))
            if footprint is None or slot_area <= 0.0:
                return iou
            return max(iou, self._intersection(polygon, footprint) / slot_area)
        if label in SMALL_VEHICLE_CLASSES:
            footprint = self._box(detection, footprint=True)
            if footprint is None:
                return iou
            area = abs(cv2.contourArea(footprint))
            inside = self._intersection(polygon, footprint) / area if area > 0.0 else 0.0
            return max(iou, inside)
        return iou

    def score_slots(
        self,
        frame: np.ndarray,
        slots: list[SlotGeometry],
        yolo_detections: list[dict],
    ) -> list[SlotReading]:
        if not slots:
            return []
        height, width = frame.shape[:2]
        mask = self._binary_mask(frame) if self._texture_fallback else None
        destination = np.array(
            [
                [0, 0],
                [WARP_WIDTH - 1, 0],
                [WARP_WIDTH - 1, WARP_HEIGHT - 1],
                [0, WARP_HEIGHT - 1],
            ],
            dtype=np.float32,
        )
        readings = []
        for slot in slots:
            polygon = self._pixel_polygon(slot, width, height)
            max_iou = max(
                (self._detection_score(polygon, detection) for detection in yolo_detections),
                default=0.0,
            )
            if max_iou >= self._iou_min:
                occupied = True
                score = max_iou
                source = 'vision_yolo'
            elif not self._texture_fallback:
                occupied = False
                score = max_iou
                source = 'yolo'
            else:
                transform = cv2.getPerspectiveTransform(polygon, destination)
                warped = cv2.warpPerspective(mask, transform, (WARP_WIDTH, WARP_HEIGHT))
                score = cv2.countNonZero(warped) / float(WARP_WIDTH * WARP_HEIGHT)
                source = 'vision_texture'
                occupied = score >= self._hi
            readings.append(
                SlotReading(
                    space_id=slot.space_id,
                    db_id=slot.db_id,
                    occupied=occupied,
                    score=float(score),
                    source=source,
                )
            )
        return readings


class OccupancyDebouncer:
    def __init__(self, consecutive_frames: int = 5):
        if consecutive_frames < 1:
            raise ValueError('consecutive_frames must be at least one')
        self._required = int(consecutive_frames)
        self._candidates: dict[str, tuple[bool, int]] = {}
        self._committed: dict[str, bool] = {}

    def update(self, readings: list[SlotReading]) -> list[SlotTransition]:
        transitions = []
        for reading in readings:
            candidate, count = self._candidates.get(
                reading.space_id, (reading.occupied, 0)
            )
            if candidate != reading.occupied:
                candidate, count = reading.occupied, 0
            count += 1
            self._candidates[reading.space_id] = (candidate, count)
            if count < self._required or self._committed.get(reading.space_id) == candidate:
                continue
            self._committed[reading.space_id] = candidate
            transitions.append(
                SlotTransition(
                    space_id=reading.space_id,
                    db_id=reading.db_id,
                    occupied=candidate,
                    score=reading.score,
                    source=reading.source,
                )
            )
        return transitions

    def reset(self) -> None:
        self._candidates.clear()
        self._committed.clear()
