"""Dashboard sessions: short access tokens plus rotating refresh tokens.

The browser holds both in httpOnly cookies (argus_common.web_auth). An access
token lives minutes; the refresh token is single use and rotated on every
refresh, bounded by an idle timeout and an absolute session lifetime.
Presenting an already-used refresh token outside a short grace window
(concurrent refreshes from two tabs) revokes the whole session: someone else
holds a copy of it.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AuthSession, User
from app.services.auth import create_access_token, session_cookie_names
from app.utils import utc_now
from argus_common.web_auth import REFRESH_COOKIE_PATH

logger = logging.getLogger(__name__)

# Two tabs refreshing at once both present the same token; the loser's
# browser already holds the winner's new cookie and simply retries.
REUSE_GRACE = timedelta(seconds=30)


class SessionError(Exception):
    """The refresh token cannot continue a session."""


@dataclass(frozen=True)
class IssuedSession:
    user: User
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _access_token(user: User) -> tuple[str, datetime]:
    ttl = timedelta(seconds=get_settings().AUTH_SESSION_ACCESS_TTL_SECONDS)
    token = create_access_token(
        {"sub": user.username, "uid": user.id, "role": user.role, "tenant_id": user.tenant_id},
        expires_delta=ttl,
    )
    return token, utc_now() + ttl


def _new_refresh_row(user: User, family_id: str, family_expires_at: datetime, user_agent: Optional[str]):
    token = secrets.token_urlsafe(32)
    idle = timedelta(seconds=get_settings().AUTH_SESSION_IDLE_SECONDS)
    row = AuthSession(
        family_id=family_id,
        user_id=user.id,
        token_hash=_hash(token),
        created_at=utc_now(),
        expires_at=min(utc_now() + idle, family_expires_at),
        family_expires_at=family_expires_at,
        user_agent=(user_agent or "")[:200] or None,
    )
    return token, row


async def start_session(db: AsyncSession, user: User, user_agent: Optional[str] = None) -> IssuedSession:
    family_expires_at = utc_now() + timedelta(seconds=get_settings().AUTH_SESSION_MAX_SECONDS)
    refresh_token, row = _new_refresh_row(user, str(uuid.uuid4()), family_expires_at, user_agent)
    db.add(row)
    await db.commit()
    access_token, access_expires_at = _access_token(user)
    return IssuedSession(user, access_token, access_expires_at, refresh_token, row.expires_at)


async def _revoke_family(db: AsyncSession, family_id: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.family_id == family_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    await db.commit()


async def rotate_session(db: AsyncSession, refresh_token: str, user_agent: Optional[str] = None) -> IssuedSession:
    row = (
        await db.execute(select(AuthSession).where(AuthSession.token_hash == _hash(refresh_token)))
    ).scalar_one_or_none()
    if row is None:
        raise SessionError("unknown refresh token")
    now = utc_now()
    if row.revoked_at is not None:
        raise SessionError("session revoked")
    if row.used_at is not None:
        if now - row.used_at > REUSE_GRACE:
            logger.warning("Refresh token reuse: revoking session family=%s user=%s", row.family_id, row.user_id)
            await _revoke_family(db, row.family_id)
            raise SessionError("refresh token reused")
        raise SessionError("refresh token already rotated")
    if now >= row.expires_at or now >= row.family_expires_at:
        raise SessionError("session expired")

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        await _revoke_family(db, row.family_id)
        raise SessionError("user inactive")

    # Claim the row atomically so two concurrent refreshes cannot both win.
    claimed = await db.execute(
        update(AuthSession)
        .where(AuthSession.id == row.id, AuthSession.used_at.is_(None))
        .values(used_at=now)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        raise SessionError("refresh token already rotated")
    new_token, new_row = _new_refresh_row(user, row.family_id, row.family_expires_at, user_agent)
    db.add(new_row)
    await db.commit()
    access_token, access_expires_at = _access_token(user)
    return IssuedSession(user, access_token, access_expires_at, new_token, new_row.expires_at)


async def end_session(db: AsyncSession, refresh_token: Optional[str]) -> None:
    if not refresh_token:
        return
    row = (
        await db.execute(select(AuthSession).where(AuthSession.token_hash == _hash(refresh_token)))
    ).scalar_one_or_none()
    if row is not None:
        await _revoke_family(db, row.family_id)


async def revoke_user_sessions(db: AsyncSession, user_id: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    await db.commit()


def _max_age(expires_at: datetime) -> int:
    return max(0, int((expires_at - utc_now()).total_seconds()))


def set_session_cookies(response: Response, issued: IssuedSession) -> None:
    names = session_cookie_names()
    response.set_cookie(
        names.access,
        issued.access_token,
        max_age=_max_age(issued.access_expires_at),
        path="/",
        secure=names.secure,
        httponly=True,
        samesite="strict",
    )
    response.set_cookie(
        names.refresh,
        issued.refresh_token,
        max_age=_max_age(issued.refresh_expires_at),
        path=REFRESH_COOKIE_PATH,
        secure=names.secure,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookies(response: Response) -> None:
    names = session_cookie_names()
    response.delete_cookie(names.access, path="/", secure=names.secure, httponly=True, samesite="strict")
    response.delete_cookie(
        names.refresh, path=REFRESH_COOKIE_PATH, secure=names.secure, httponly=True, samesite="strict"
    )
