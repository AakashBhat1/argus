"""Import a legacy zones JSON file into one tenant.

    python -m app.cli.import_zones --tenant-id 1
    python -m app.cli.import_zones --tenant-id 1 --file path/to/zones_config.json

The old file was shared by every tenant and records no owner, so the operator
names the tenant. Camera ids that do not belong to it are dropped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select

from app.config import get_settings
from app.database import get_session_factory
from app.models import Camera, RoiZoneRecord
from app.services import audit
from argus_common.audit import AuditActor
from argus_vision.roi import parse_zone


class ZoneImportError(Exception):
    """The file cannot be imported as requested."""


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ZoneImportError(f"cannot read {path}: {exc}") from exc
    zones = payload.get("zones", []) if isinstance(payload, dict) else payload
    if not isinstance(zones, list):
        raise ZoneImportError(f"{path} does not contain a list of zones")
    return zones


async def import_zones(path: Path, tenant_id: str, session_factory: Any = None) -> int:
    settings = get_settings()
    zones = _read(path)
    factory = session_factory or get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(func.count(RoiZoneRecord.id)).where(RoiZoneRecord.tenant_id == tenant_id))
        ).scalar()
        if existing:
            raise ZoneImportError(f"tenant {tenant_id!r} already has zones; refusing to mix them with the file")
        owned = set((await session.execute(select(Camera.id).where(Camera.tenant_id == tenant_id))).scalars())
        actor = AuditActor.system(tenant_id, name="cli:import_zones")
        for index, raw in enumerate(zones, start=1):
            try:
                zone = parse_zone(
                    raw,
                    next_id=index,
                    default_threshold=settings.ROI_DEFAULT_DWELL_SEC,
                    default_reference_width=settings.ROI_REFERENCE_WIDTH,
                    default_reference_height=settings.ROI_REFERENCE_HEIGHT,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ZoneImportError(f"zone #{index} is invalid: {exc}") from exc
            cameras = sorted(zone.camera_ids & owned) if zone.camera_ids else None
            record = RoiZoneRecord(
                tenant_id=tenant_id,
                name=zone.name[:100],
                points=[[round(float(x), 6), round(float(y), 6)] for x, y in zone.normalized_points],
                threshold_sec=zone.threshold_sec,
                color=list(zone.color),
                camera_ids=cameras or None,
                zone_type=zone.zone_type,
                armed_schedule=zone.armed_schedule.to_dict(),
                allowed_classes=sorted(zone.allowed_classes),
            )
            session.add(record)
            await session.flush()
            audit.record(
                session, None, actor, "zone.import", "zone", record.id,
                {"name": record.name, "source": path.name, "camera_ids": cameras},
            )
        await session.commit()
    return len(zones)


def main(argv: Optional[list[str]] = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Import a legacy ROI zones JSON file into one tenant.")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--file", default=str(settings.resolve_path(settings.ROI_ZONES_CONFIG_PATH)))
    args = parser.parse_args(argv)
    try:
        count = asyncio.run(import_zones(Path(args.file), args.tenant_id))
    except ZoneImportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"imported {count} zone(s) into tenant {args.tenant_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
