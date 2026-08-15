'''Deterministic parking anomaly rules over existing pipeline data.'''

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.detection.roi import RoiZone
from app.models import Alert, AlertSeverity, DetectedPlate, ParkingSpace
from app.services.parking_occupancy import SlotGeometry, SlotTransition
from app.services.trajectory import TrajectoryFeatures
from app.utils import utc_now


class ParkingAnomalyDetector:
    def __init__(self, settings=None):
        self._settings = settings or get_settings()
        if (
            self._settings.PARKING_GHOST_PLATE_LOOKBACK_MIN
            <= self._settings.PARKING_GHOST_OCCUPANCY_MIN
        ):
            raise ValueError('ghost plate lookback must exceed occupancy threshold')
        self._cooldown_map: dict[tuple[str, str], float] = {}
        self._transition_times: dict[tuple[str, str], deque[datetime]] = defaultdict(deque)

    @property
    def enabled(self) -> bool:
        return bool(self._settings.PARKING_ANOMALY_ENABLED)

    def _can_emit(
        self,
        rule: str,
        subject_id: str,
        now: datetime,
        *,
        scope: str = '',
    ) -> bool:
        scoped_subject = f'{scope}:{subject_id}' if scope else subject_id
        key = (rule, scoped_subject)
        timestamp = self._cooldown_map.get(key, 0.0)
        aware_utc = (
            now.replace(tzinfo=timezone.utc)
            if now.tzinfo is None
            else now.astimezone(timezone.utc)
        )
        if aware_utc.timestamp() - timestamp < float(
            self._settings.PARKING_ANOMALY_COOLDOWN_SEC
        ):
            return False
        self._cooldown_map[key] = aware_utc.timestamp()
        return True

    def cleanup_cooldowns(self, now: datetime | None = None) -> None:
        current = (now or utc_now()).replace(tzinfo=timezone.utc).timestamp()
        max_age = float(self._settings.PARKING_ANOMALY_COOLDOWN_SEC) * 2
        for key, timestamp in list(self._cooldown_map.items()):
            if current - timestamp > max_age:
                del self._cooldown_map[key]

    def is_quiet_hours(self, now: datetime) -> bool:
        aware_utc = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
        local_hour = aware_utc.astimezone(
            ZoneInfo(self._settings.PARKING_QUIET_HOURS_TZ)
        ).hour
        start = int(self._settings.PARKING_QUIET_HOURS_START)
        end = int(self._settings.PARKING_QUIET_HOURS_END)
        if start == end:
            return True
        if start > end:
            return local_hour >= start or local_hour < end
        return start <= local_hour < end

    @staticmethod
    def _alert(
        *,
        camera_id: str,
        tenant_id: str,
        rule: str,
        subject_id: str,
        severity: str,
        description: str,
        now: datetime,
    ) -> Alert:
        return Alert(
            camera_id=camera_id,
            tenant_id=tenant_id,
            type=rule,
            severity=severity,
            trigger_condition=rule,
            description=description,
            timestamp=now,
            metadata_={
                'source': 'parking_anomaly',
                'rule': rule,
                'subject_id': subject_id,
            },
        )

    async def detect_ghost_occupancy(
        self,
        db: AsyncSession,
        *,
        camera_id: str,
        tenant_id: str,
        now: datetime | None = None,
    ) -> list[Alert]:
        if not self.enabled:
            return []
        event_time = now or utc_now()
        occupied_cutoff = event_time - timedelta(
            minutes=float(self._settings.PARKING_GHOST_OCCUPANCY_MIN)
        )
        plate_cutoff = event_time - timedelta(
            minutes=float(self._settings.PARKING_GHOST_PLATE_LOOKBACK_MIN)
        )
        plate = (
            await db.execute(
                select(DetectedPlate.id)
                .where(
                    DetectedPlate.tenant_id == tenant_id,
                    DetectedPlate.timestamp >= plate_cutoff,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if plate is not None:
            return []
        spaces = (
            await db.execute(
                select(ParkingSpace).where(
                    ParkingSpace.tenant_id == tenant_id,
                    ParkingSpace.camera_id == camera_id,
                    ParkingSpace.is_occupied.is_(True),
                    ParkingSpace.last_state_change <= occupied_cutoff,
                )
            )
        ).scalars().all()
        alerts = []
        for space in spaces:
            if not self._can_emit(
                'ghost_occupancy',
                space.space_id,
                event_time,
                scope=f'{tenant_id}:{camera_id}',
            ):
                continue
            alerts.append(
                self._alert(
                    camera_id=camera_id,
                    tenant_id=tenant_id,
                    rule='ghost_occupancy',
                    subject_id=space.space_id,
                    severity=AlertSeverity.MEDIUM.value,
                    description=f'Occupied space {space.space_id} has no recent gate plate.',
                    now=event_time,
                )
            )
        return alerts

    def detect_lane_loitering(
        self,
        *,
        camera_id: str,
        tenant_id: str,
        features: TrajectoryFeatures,
        intent_type: str,
        now: datetime | None = None,
    ) -> list[Alert]:
        event_time = now or utc_now()
        if (
            not self.enabled
            or intent_type != 'loitering'
            or features.duration_sec < float(self._settings.PARKING_LOITER_MIN_SEC)
            or not self._can_emit(
                'lane_loitering',
                str(features.object_id),
                event_time,
                scope=f'{tenant_id}:{camera_id}',
            )
        ):
            return []
        return [
            self._alert(
                camera_id=camera_id,
                tenant_id=tenant_id,
                rule='lane_loitering',
                subject_id=str(features.object_id),
                severity=AlertSeverity.MEDIUM.value,
                description=f'Object {features.object_id} loitered in the parking lane.',
                now=event_time,
            )
        ]

    def detect_car_hopping(
        self,
        *,
        camera_id: str,
        tenant_id: str,
        features: TrajectoryFeatures,
        slots: list[SlotGeometry],
        frame_shape: tuple[int, int] | tuple[int, int, int],
        now: datetime | None = None,
    ) -> list[Alert]:
        event_time = now or utc_now()
        if (
            not self.enabled
            or features.stationary_ratio
            < float(self._settings.PARKING_CAR_HOP_MIN_STATIONARY)
        ):
            return []
        height, width = frame_shape[:2]
        visited = set()
        for slot in slots:
            zone = RoiZone(0, slot.space_id, slot.polygon, 0.0, (0, 0, 0))
            for point in features.trajectory_points:
                x, y = float(point[0]), float(point[1])
                if max(abs(x), abs(y)) <= 1.0:
                    x, y = x * width, y * height
                if zone.contains_pixel(x, y, width, height):
                    visited.add(slot.db_id)
                    break
        if (
            len(visited) < int(self._settings.PARKING_CAR_HOP_MIN_SLOTS)
            or not self._can_emit(
                'car_hopping',
                str(features.object_id),
                event_time,
                scope=f'{tenant_id}:{camera_id}',
            )
        ):
            return []
        return [
            self._alert(
                camera_id=camera_id,
                tenant_id=tenant_id,
                rule='car_hopping',
                subject_id=str(features.object_id),
                severity=AlertSeverity.HIGH.value,
                description=f'Object {features.object_id} stopped across {len(visited)} spaces.',
                now=event_time,
            )
        ]

    def detect_after_hours_churn(
        self,
        *,
        camera_id: str,
        tenant_id: str,
        transitions: list[SlotTransition],
        now: datetime | None = None,
    ) -> list[Alert]:
        event_time = now or utc_now()
        if not self.enabled or not transitions or not self.is_quiet_hours(event_time):
            return []
        key = (tenant_id, camera_id)
        history = self._transition_times[key]
        history.extend(event_time for _ in transitions)
        cutoff = event_time - timedelta(
            minutes=float(self._settings.PARKING_CHURN_WINDOW_MIN)
        )
        while history and history[0] < cutoff:
            history.popleft()
        if (
            len(history) < int(self._settings.PARKING_CHURN_THRESHOLD)
            or not self._can_emit(
                'after_hours_churn',
                camera_id,
                event_time,
                scope=tenant_id,
            )
        ):
            return []
        return [
            self._alert(
                camera_id=camera_id,
                tenant_id=tenant_id,
                rule='after_hours_churn',
                subject_id=camera_id,
                severity=AlertSeverity.MEDIUM.value,
                description=f'{len(history)} parking transitions occurred during quiet hours.',
                now=event_time,
            )
        ]


parking_anomaly_detector = ParkingAnomalyDetector()
