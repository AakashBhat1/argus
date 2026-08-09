"""ParkBot chat endpoints — read for active users, commands for admin."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.schemas import ParkingChatRequest, ParkingChatResponse
from app.services.auth import get_current_active_user, require_admin
from app.services.parking_assistant import parking_assistant

router = APIRouter(prefix="/parking", tags=["parking-chat"])


@router.post("/chat", response_model=ParkingChatResponse)
async def parking_chat(
    body: ParkingChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Read-mode chat for any active user.

    For command mode, clients should call ``/chat/command`` (admin only).
    If ``body.command`` is true here, non-admins get a 403 via require_admin
    path delegated below only when flag is set — we re-check role explicitly.
    """
    if body.command:
        # Defer to admin gate
        from fastapi import HTTPException

        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Not enough privileges")
        result = await parking_assistant.chat_command(
            db,
            current_user.tenant_id,
            body.message,
            actor_user_id=current_user.id,
        )
    else:
        result = await parking_assistant.chat_read(
            db, current_user.tenant_id, body.message
        )

    return ParkingChatResponse(
        role=result.get("role", "assistant"),
        mode=result.get("mode", "read"),
        content=result.get("content", ""),
        command=result.get("command"),
        executed=bool(result.get("executed")),
        result=result.get("result"),
        error=result.get("error"),
    )


@router.post("/chat/command", response_model=ParkingChatResponse)
async def parking_chat_command(
    body: ParkingChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    result = await parking_assistant.chat_command(
        db,
        current_user.tenant_id,
        body.message,
        actor_user_id=current_user.id,
    )
    return ParkingChatResponse(
        role=result.get("role", "assistant"),
        mode=result.get("mode", "command"),
        content=result.get("content", ""),
        command=result.get("command"),
        executed=bool(result.get("executed")),
        result=result.get("result"),
        error=result.get("error"),
    )
