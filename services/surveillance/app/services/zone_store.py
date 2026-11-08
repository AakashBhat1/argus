"""Tenant-scoped ROI zones for the frame loop.

The pipelines evaluate zones on every frame and must not wait on the database,
so they read an in-memory snapshot. The zones router refreshes a tenant right
after each commit; a background task reloads everything periodically so that
changes made by another worker arrive within ZONE_REFRESH_SECONDS.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable, Mapping

from sqlalchemy import select

from app import database
from app.config import get_settings
from app.models import RoiZoneRecord
from argus_vision.roi import RoiZone, parse_zone

logger = logging.getLogger(__name__)


def record_to_dict(row: RoiZoneRecord) -> dict[str, Any]:
    """The zone's stored fields, in the shape ``parse_zone`` and the API read."""
    return {
        "zone_id": row.id,
        "name": row.name,
        "points": row.points,
        "threshold_sec": row.threshold_sec,
        "color": row.color,
        "camera_ids": row.camera_ids,
        "zone_type": row.zone_type,
        "armed_schedule": row.armed_schedule,
        "allowed_classes": row.allowed_classes,
    }


def _parse(rows: Iterable[RoiZoneRecord]) -> tuple[RoiZone, ...]:
    settings = get_settings()
    zones: list[RoiZone] = []
    for row in rows:
        try:
            zones.append(
                parse_zone(
                    record_to_dict(row),
                    next_id=row.id,
                    default_threshold=settings.ROI_DEFAULT_DWELL_SEC,
                    default_reference_width=settings.ROI_REFERENCE_WIDTH,
                    default_reference_height=settings.ROI_REFERENCE_HEIGHT,
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.exception("Skipping unreadable ROI zone id=%s tenant=%s", row.id, row.tenant_id)
    return tuple(zones)


class TenantZones:
    """What one pipeline sees: its own tenant's zones, nothing else."""

    def __init__(self, store: "ZoneStore", tenant_id: str) -> None:
        self._store = store
        self._tenant_id = str(tenant_id)

    def zones_for_camera(self, camera_id: str) -> list[RoiZone]:
        return [zone for zone in self._store.zones(self._tenant_id) if zone.applies_to_camera(camera_id)]


class ZoneStore:
    def __init__(self) -> None:
        # Replaced, never mutated: readers on other threads see a whole snapshot.
        self._by_tenant: Mapping[str, tuple[RoiZone, ...]] = {}

    def zones(self, tenant_id: str) -> tuple[RoiZone, ...]:
        return self._by_tenant.get(str(tenant_id), ())

    def for_tenant(self, tenant_id: str) -> TenantZones:
        return TenantZones(self, tenant_id)

    def replace_tenant(self, tenant_id: str, rows: Iterable[RoiZoneRecord]) -> None:
        self._by_tenant = {**self._by_tenant, str(tenant_id): _parse(rows)}

    async def load_all(self, session_factory: Any = None) -> None:
        factory = session_factory or database.get_session_factory()
        async with factory() as session:
            rows = (await session.execute(select(RoiZoneRecord).order_by(RoiZoneRecord.id))).scalars().all()
        grouped: dict[str, list[RoiZoneRecord]] = {}
        for row in rows:
            grouped.setdefault(str(row.tenant_id), []).append(row)
        self._by_tenant = {tenant: _parse(items) for tenant, items in grouped.items()}


zone_store = ZoneStore()


async def zone_refresh_worker() -> None:
    interval = max(1, int(get_settings().ZONE_REFRESH_SECONDS))
    while True:
        await asyncio.sleep(interval)
        try:
            await zone_store.load_all()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep the last good snapshot: stale zones beat no zones.
            logger.exception("ROI zone refresh failed; keeping the previous zones")
