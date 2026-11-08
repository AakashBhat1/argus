"""``GET <path>``: the admin-only, tenant-scoped audit log endpoint.

Both services expose the same contract; each passes its own model, session
dependency and admin dependency (anything with a ``tenant_id``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional, Type

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from argus_common.audit import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AuditEventMixin,
    AuditPage,
    AuditQueryError,
    list_audit_events,
)


def build_audit_router(
    *,
    model: Type[AuditEventMixin],
    get_db: Callable[..., Any],
    require_admin: Callable[..., Any],
    path: str,
    tags: Optional[list[str]] = None,
) -> APIRouter:
    router = APIRouter(tags=tags or ["audit"])

    @router.get(path, response_model=AuditPage)
    async def read_audit_log(
        action: Optional[str] = Query(default=None, max_length=64, description="exact, or prefix ending in .*"),
        actor: Optional[str] = Query(default=None, max_length=160),
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        cursor: Optional[str] = Query(default=None, max_length=200),
        db: AsyncSession = Depends(get_db),
        admin: Any = Depends(require_admin),
    ) -> AuditPage:
        try:
            return await list_audit_events(
                db,
                model,
                tenant_id=str(admin.tenant_id),
                action=action,
                actor=actor,
                since=since,
                until=until,
                limit=limit,
                cursor=cursor,
            )
        except AuditQueryError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    return router
