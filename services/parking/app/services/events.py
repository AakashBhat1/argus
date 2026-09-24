"""Events parking publishes to (and accepts from) peer services.

``publish`` stages an event in the outbox inside the caller's transaction,
so the event exists if and only if the state change it describes committed.
"""

from __future__ import annotations

from typing import Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Alert, OutboxEvent
from argus_common.events import (
    EventEnvelope,
    ParkingAlertRaised,
    VehicleGatePassed,
    VehicleTheftSuspected,
    enqueue,
)

SOURCE = "parking"


def _destinations() -> list[str]:
    # Standalone parking (no surveillance peer configured) publishes nothing.
    return ["surveillance"] if get_settings().SURVEILLANCE_INTERNAL_URL else []


def publish(session: AsyncSession, event_type: str, tenant_id: str, data) -> Optional[EventEnvelope]:
    destinations = _destinations()
    if not destinations:
        return None
    envelope = EventEnvelope.create(event_type, SOURCE, tenant_id, data)
    enqueue(session, OutboxEvent, envelope, destinations)
    return envelope


async def publish_alerts(session: AsyncSession, alerts: list[Alert]) -> None:
    """Forward parking alerts to the security console (after they have ids)."""
    if not alerts or not _destinations():
        return
    await session.flush()
    for alert in alerts:
        metadata: Mapping = alert.metadata_ or {}
        publish(
            session,
            "parking.alert",
            alert.tenant_id,
            ParkingAlertRaised(
                alert_id=str(alert.id),
                alert_type=str(alert.type)[:100],
                severity=str(alert.severity),
                camera_id=alert.camera_id,
                description=str(alert.description or alert.type)[:2000],
                space_id=_short(metadata.get("space_id"), 50),
                plate=_short(metadata.get("plate_text"), 20),
            ),
        )


def publish_gate_pass(
    session: AsyncSession,
    *,
    tenant_id: str,
    plate: str,
    camera_id: str,
    direction: str,
    profile_type: Optional[str],
    decision: str = "unknown",
    identity_id: Optional[str] = None,
    identity_label: Optional[str] = None,
) -> None:
    settings = get_settings()
    profile = (profile_type or "").lower()
    authorized_types = {p.lower() for p in settings.PARKING_AUTHORIZED_PROFILE_TYPES}
    publish(
        session,
        "vehicle.gate_passed",
        tenant_id,
        VehicleGatePassed(
            plate=plate,
            direction="exit" if direction == "exit" else "entry",
            camera_id=camera_id,
            profile_type=profile or None,
            authorized=profile in authorized_types,
            threat=profile == "blacklist",
            decision=decision,
            identity_id=identity_id,
            identity_label=identity_label,
        ),
    )


def publish_theft_suspected(
    session: AsyncSession,
    *,
    tenant_id: str,
    plate: str,
    reason: str,
    camera_id: Optional[str] = None,
    space_id: Optional[str] = None,
    identity_id: Optional[str] = None,
) -> None:
    publish(
        session,
        "vehicle.theft_suspected",
        tenant_id,
        VehicleTheftSuspected(
            plate=plate,
            camera_id=camera_id,
            space_id=space_id,
            reason=reason[:500],
            identity_id=identity_id,
        ),
    )


def _short(value, limit: int) -> Optional[str]:
    return None if value is None else str(value)[:limit]
