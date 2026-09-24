"""Outbox publishing and delivery for events surveillance emits."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.config import get_settings
from app.mesh import OUTBOUND_SCOPES, mesh
from app.models import OutboxEvent
from argus_common.events import ArmModeChanged, EventEnvelope, OutboxDispatcher, enqueue

logger = logging.getLogger(__name__)

SOURCE = "surveillance"


def _peer_urls() -> dict[str, str]:
    settings = get_settings()
    return {"parking": settings.PARKING_INTERNAL_URL} if settings.PARKING_INTERNAL_URL else {}


def publish_arm_mode(session: AsyncSession, tenant_id: str, mode: str, actor: Optional[str]) -> None:
    destinations = [peer for peer in _peer_urls() if peer == "parking"]
    if not destinations:
        return
    envelope = EventEnvelope.create(
        "security.arm_mode_changed", SOURCE, tenant_id, ArmModeChanged(mode=mode, actor=actor)
    )
    enqueue(session, OutboxEvent, envelope, destinations)


class OutboxRunner:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._clients: list = []
        self.dispatcher: Optional[OutboxDispatcher] = None

    def start(self) -> None:
        peers = _peer_urls()
        if not peers:
            logger.info("No peer services configured; outbox delivery disabled")
            return
        clients = {
            peer: mesh.client(peer=peer, base_url=url, scopes=OUTBOUND_SCOPES[peer])
            for peer, url in peers.items()
        }
        self._clients = list(clients.values())
        self.dispatcher = OutboxDispatcher(
            database.get_session_factory(),
            OutboxEvent,
            clients,
            poll_interval=get_settings().OUTBOX_POLL_INTERVAL_SEC,
        )
        self._task = asyncio.create_task(self.dispatcher.run_forever(self._stop))

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        for client in self._clients:
            await client.aclose()


outbox_runner = OutboxRunner()
