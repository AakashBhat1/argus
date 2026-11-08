"""Periodic purge of audit rows past AUDIT_RETENTION_DAYS and dead ParkBot proposals."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import delete, or_

from app import database
from app.config import get_settings
from app.models import AuditEvent, ParkbotProposal
from app.utils import utc_now
from argus_common.audit import purge_audit_events

logger = logging.getLogger(__name__)


async def purge_once() -> tuple[int, int]:
    """Delete expired audit rows and spent/expired proposals; returns both counts."""
    settings = get_settings()
    async with database.get_session_factory()() as session:
        audit_count = await purge_audit_events(session, AuditEvent, retention_days=settings.AUDIT_RETENTION_DAYS)
        proposals = await session.execute(
            delete(ParkbotProposal).where(
                or_(ParkbotProposal.expires_at < utc_now(), ParkbotProposal.consumed_at.is_not(None))
            )
        )
        await session.commit()
    return audit_count, int(proposals.rowcount or 0)


async def retention_worker() -> None:
    interval = get_settings().AUDIT_PURGE_INTERVAL_SECONDS
    while True:
        try:
            audit_count, proposal_count = await purge_once()
            logger.info("Retention: deleted audit_events=%s parkbot_proposals=%s", audit_count, proposal_count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Retention purge failed; retrying next interval")
        await asyncio.sleep(interval)
