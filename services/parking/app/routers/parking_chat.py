"""ParkBot chat endpoints: read for active users, commands for admins.

Commands are two-step. ``/chat/command`` (or ``/chat`` with command=true)
only *proposes* a validated action; ``/chat/confirm`` runs it, once, for the
admin who asked. Every proposal, rejection and execution is audited.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import ParkingChatConfirmRequest, ParkingChatRequest, ParkingChatResponse
from app.services.auth import Principal as User, get_current_active_user, require_admin
from app.services.parking_assistant import ProposalError, parking_assistant

router = APIRouter(prefix="/parking", tags=["parking-chat"])


def _response(result: dict[str, Any], default_mode: str) -> ParkingChatResponse:
    return ParkingChatResponse(
        role=result.get("role", "assistant"),
        mode=result.get("mode", default_mode),
        content=result.get("content", ""),
        command=result.get("command"),
        executed=bool(result.get("executed")),
        result=result.get("result"),
        error=result.get("error"),
        requires_confirmation=bool(result.get("requires_confirmation")),
        proposal_id=result.get("proposal_id"),
        expires_at=result.get("expires_at"),
    )


@router.post("/chat", response_model=ParkingChatResponse)
async def parking_chat(
    body: ParkingChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Read-mode chat for any active user; ``command=true`` is admin-only."""
    if body.command:
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Not enough privileges")
        result = await parking_assistant.chat_command(
            db, current_user.tenant_id, body.message, actor=current_user, request=request
        )
        return _response(result, "command")
    result = await parking_assistant.chat_read(db, current_user.tenant_id, body.message)
    return _response(result, "read")


@router.post("/chat/command", response_model=ParkingChatResponse)
async def parking_chat_command(
    body: ParkingChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await parking_assistant.chat_command(
        db, current_user.tenant_id, body.message, actor=current_user, request=request
    )
    return _response(result, "command")


@router.post("/chat/confirm", response_model=ParkingChatResponse)
async def parking_chat_confirm(
    body: ParkingChatConfirmRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        result = await parking_assistant.confirm(
            db, current_user.tenant_id, body.proposal_id, actor=current_user, request=request
        )
    except ProposalError as exc:
        # Keep the audit row of the refused confirmation (a replay or a stale tab).
        await db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _response(result, "command")
