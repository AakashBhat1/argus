"""Surveillance's audit helper: ``record`` stages a row in the caller's transaction.

Call it before the commit of the change it describes. See argus_common.audit.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Union

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from argus_common.audit import AuditActor, add_audit_event
from app.models import AuditEvent


def source_ip(request: Optional[Request]) -> Optional[str]:
    """The client address as uvicorn resolved it (edge-only forwarded headers)."""
    return request.client.host if request is not None and request.client else None


def record(
    db: AsyncSession,
    request: Optional[Request],
    actor: Union[AuditActor, Any],
    action: str,
    target_type: Optional[str] = None,
    target_id: Any = None,
    details: Optional[Mapping[str, Any]] = None,
) -> AuditEvent:
    """Stage one audit row; ``actor`` is a User or an AuditActor."""
    who = actor if isinstance(actor, AuditActor) else AuditActor.user(actor)
    return add_audit_event(
        db,
        AuditEvent,
        actor=who,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
        source_ip=source_ip(request),
    )
