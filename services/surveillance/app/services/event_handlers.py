"""Apply events received from peer services.

Handlers run inside the same transaction that records the event id in the
inbox, so a redelivered event is a no-op and a failed handler leaves the
event unrecorded (the sender retries).
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Alert, AlertSeverity
from app.services.authorization import authorization_registry
from argus_common.events import (
    EventEnvelope,
    ParkingAlertRaised,
    VehicleGatePassed,
    VehicleTheftSuspected,
)

logger = logging.getLogger(__name__)

# (alert payload for WebSocket broadcast) collected per request
Broadcasts = list[dict]


def _alert_payload(alert: Alert) -> dict:
    return {
        "alert_id": str(alert.id),
        "camera_id": alert.camera_id,
        "source": alert.source,
        "type": alert.type,
        "severity": alert.severity,
        "description": alert.description,
        "metadata": alert.metadata_ or {},
        "timestamp": alert.timestamp.isoformat() + "Z" if alert.timestamp else None,
    }


async def _vehicle_gate_passed(
    session: AsyncSession, envelope: EventEnvelope, broadcasts: Broadcasts
) -> None:
    data: VehicleGatePassed = envelope.validated_data()  # type: ignore[assignment]
    if data.direction == "entry" and data.authorized and not data.threat:
        # Persons stepping out of *a* vehicle shortly after an authorized
        # arrival inherit it (see AuthorizationRegistry.resolve).
        authorization_registry.register_vehicle_arrival(
            envelope.tenant_id,
            plate_text=data.plate,
            profile_type=data.profile_type,
            label=data.identity_label,
        )
    if data.threat:
        alert = Alert(
            camera_id=None,
            tenant_id=envelope.tenant_id,
            source=envelope.source,
            source_ref=envelope.id,
            type="blacklisted_vehicle",
            severity=AlertSeverity.HIGH.value,
            trigger_condition=f"Plate {data.plate} ({data.direction}) is blacklisted",
            description=f"Blacklisted vehicle {data.plate} at a parking gate ({data.direction}).",
            metadata_={"plate": data.plate, "parking_camera_id": data.camera_id, "decision": data.decision},
        )
        session.add(alert)
        await session.flush()
        broadcasts.append(_alert_payload(alert))


async def _parking_alert(
    session: AsyncSession, envelope: EventEnvelope, broadcasts: Broadcasts
) -> None:
    data: ParkingAlertRaised = envelope.validated_data()  # type: ignore[assignment]
    alert = Alert(
        camera_id=None,
        tenant_id=envelope.tenant_id,
        source=envelope.source,
        source_ref=data.alert_id,
        type=data.alert_type,
        severity=data.severity,
        trigger_condition=f"parking:{data.alert_type}",
        description=data.description,
        metadata_={
            "parking_camera_id": data.camera_id,
            "space_id": data.space_id,
            "plate": data.plate,
        },
    )
    session.add(alert)
    await session.flush()
    broadcasts.append(_alert_payload(alert))


async def _vehicle_theft_suspected(
    session: AsyncSession, envelope: EventEnvelope, broadcasts: Broadcasts
) -> None:
    data: VehicleTheftSuspected = envelope.validated_data()  # type: ignore[assignment]
    alert = Alert(
        camera_id=None,
        tenant_id=envelope.tenant_id,
        source=envelope.source,
        source_ref=envelope.id,
        type="vehicle_theft_suspected",
        severity=AlertSeverity.CRITICAL.value,
        trigger_condition=f"Plate {data.plate}: {data.reason}",
        description=f"Possible theft of vehicle {data.plate}: {data.reason} Verify on camera.",
        metadata_={
            "plate": data.plate,
            "parking_camera_id": data.camera_id,
            "space_id": data.space_id,
            "identity_id": data.identity_id,
        },
    )
    session.add(alert)
    await session.flush()
    broadcasts.append(_alert_payload(alert))


HANDLERS: dict[str, Callable[[AsyncSession, EventEnvelope, Broadcasts], Awaitable[None]]] = {
    "vehicle.gate_passed": _vehicle_gate_passed,
    "parking.alert": _parking_alert,
    "vehicle.theft_suspected": _vehicle_theft_suspected,
}
