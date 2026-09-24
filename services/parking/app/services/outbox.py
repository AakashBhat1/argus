"""Delivery of events parking emits (to surveillance)."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app import database
from app.config import get_settings
from app.mesh import OUTBOUND_SCOPES, mesh
from app.models import OutboxEvent
from argus_common.events import OutboxDispatcher

logger = logging.getLogger(__name__)


class OutboxRunner:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._clients: list = []
        self.dispatcher: Optional[OutboxDispatcher] = None

    def start(self) -> None:
        settings = get_settings()
        if not settings.SURVEILLANCE_INTERNAL_URL:
            logger.info("No surveillance peer configured; parking runs standalone")
            return
        client = mesh.client(
            peer="surveillance",
            base_url=settings.SURVEILLANCE_INTERNAL_URL,
            scopes=OUTBOUND_SCOPES["surveillance"],
        )
        self._clients = [client]
        self.dispatcher = OutboxDispatcher(
            database.get_session_factory(),
            OutboxEvent,
            {"surveillance": client},
            poll_interval=settings.OUTBOX_POLL_INTERVAL_SEC,
        )
        self._task = asyncio.create_task(self.dispatcher.run_forever(self._stop))

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        for client in self._clients:
            await client.aclose()


outbox_runner = OutboxRunner()
