"""Tenant-aware parking space seeder.

Seeds A-01 … C-08 per floor layout (floors G/1/2 × zones A/B/C).
Never seeds a global set — always scoped to a single tenant_id.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ParkingSpace, ParkingSession, generate_uuid
from app.utils import utc_now

logger = logging.getLogger(__name__)

# 24 spaces: 8 per floor across G, 1, 2; zones A (1-3), B (4-6), C (7-8)
# Labels match blueprint examples (A-01 … F2-08 style via floor prefix).
_SPACE_LAYOUT: list[tuple[str, str, str]] = []  # (space_id, zone, floor)

for floor, prefix in (("G", "G"), ("1", "F1"), ("2", "F2")):
    for idx in range(1, 9):
        space_id = f"{prefix}-{idx:02d}"
        if idx <= 3:
            zone = "A"
        elif idx <= 6:
            zone = "B"
        else:
            zone = "C"
        _SPACE_LAYOUT.append((space_id, zone, floor))

# Also include A-01…C-08 aliases used in plan examples (zone-prefixed, floor G)
# Only the G/F1/F2 layout is seeded to match the existing parking UI contract.


async def seed_parking_spaces_for_tenant(
    db: AsyncSession,
    tenant_id: str,
    *,
    start_session: bool = True,
) -> int:
    """Insert default parking spots for *tenant_id* if none exist.

    Returns the number of spaces inserted (0 if already seeded).
    """
    if not tenant_id:
        raise ValueError("tenant_id is required")

    count_result = await db.execute(
        select(func.count(ParkingSpace.id)).where(ParkingSpace.tenant_id == tenant_id)
    )
    existing = int(count_result.scalar_one() or 0)
    if existing > 0:
        logger.info(
            "Parking seeder: tenant=%s already has %s spaces — skip",
            tenant_id,
            existing,
        )
        return 0

    rows = [
        ParkingSpace(
            id=generate_uuid(),
            tenant_id=tenant_id,
            space_id=space_id,
            zone=zone,
            floor=floor,
            is_occupied=False,
            vehicle_id=None,
            entry_time=None,
        )
        for space_id, zone, floor in _SPACE_LAYOUT
    ]
    db.add_all(rows)

    if start_session:
        db.add(
            ParkingSession(
                id=generate_uuid(),
                tenant_id=tenant_id,
                start_time=utc_now(),
                end_time=None,
                plates_detected=0,
                spaces_used=0,
            )
        )

    await db.flush()
    logger.info(
        "Parking seeder: inserted %s spaces for tenant=%s",
        len(rows),
        tenant_id,
    )
    return len(rows)
