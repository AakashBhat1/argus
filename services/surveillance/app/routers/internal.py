"""Internal API for peer services (mTLS + service token; never public).

The public reverse proxy returns 404 for /internal/*; only the internal
mutual-TLS listener forwards here, and every route additionally requires a
scoped, single-use service token.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app import database
from app.mesh import ACCEPTED_EVENTS, mesh
from app.models import InboxEvent
from app.services.event_handlers import HANDLERS
from app.services.websocket_manager import ws_manager
from argus_common.events import EventEnvelope, EventError, record_inbox
from argus_common.tokens import ServicePrincipal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1", tags=["internal"], include_in_schema=False)

MAX_EVENT_BYTES = 64 * 1024


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def receive_event(
    request: Request,
    principal: ServicePrincipal = Depends(mesh.auth.require("events:publish")),
):
    body = await request.body()
    if len(body) > MAX_EVENT_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "event too large")
    try:
        envelope = EventEnvelope.from_dict(await request.json())
    except (EventError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    # A peer may only speak for itself, and only publish what it owns.
    if envelope.source != principal.service:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "event source does not match caller")
    if envelope.type not in ACCEPTED_EVENTS.get(principal.service, set()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "event type not accepted from this peer")

    broadcasts: list[dict] = []
    async with database.get_session_factory()() as session:
        if not await record_inbox(session, InboxEvent, envelope):
            return JSONResponse({"status": "duplicate"}, status_code=status.HTTP_200_OK)
        await HANDLERS[envelope.type](session, envelope, broadcasts)
        await session.commit()

    for payload in broadcasts:
        await ws_manager.broadcast_alert(payload, tenant_id=envelope.tenant_id)
    logger.info("Accepted %s from %s (tenant=%s)", envelope.type, envelope.source, envelope.tenant_id)
    return {"status": "accepted"}


@router.get("/health")
async def internal_health(principal: ServicePrincipal = Depends(mesh.auth.require())):
    return {"status": "ok", "caller": principal.service}
