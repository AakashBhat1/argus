import hmac
import logging
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models import User, UserRole
from app.schemas import Token, UserResponse, UserCreate
from app.services.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    get_current_active_user,
    get_optional_current_user,
    get_password_hash,
    session_cookie_names,
    verify_password
)
from app.config import get_settings
from app.services.login_attempts import login_attempt_limiter
from app.services.sessions import (
    IssuedSession,
    SessionError,
    clear_session_cookies,
    end_session,
    rotate_session,
    set_session_cookies,
    start_session,
)
from argus_common.web_auth import CsrfError, check_same_origin

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)
DUMMY_PASSWORD_HASH = get_password_hash("argus-dummy-password-that-never-authenticates")


def _source_ip(request: Request) -> str:
    """The client address. uvicorn derives it from X-Forwarded-For only when
    the connection comes from the edge proxy (FORWARDED_ALLOW_IPS); request
    headers are never trusted here, or clients could pick their address and
    slip past the per-IP login limit."""
    return request.client.host if request.client else "unknown"


async def _authenticate(request: Request, username: str, password: str, db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()

    password_hash = user.hashed_password if user is not None else DUMMY_PASSWORD_HASH
    password_matches = verify_password(password, password_hash)
    credentials_valid = user is not None and password_matches and bool(user.is_active)
    source_ip = _source_ip(request)
    decision = await login_attempt_limiter.decide(
        db,
        source_ip=source_ip,
        username=username,
        credentials_valid=credentials_valid,
    )

    if not decision.allowed:
        logger.warning(
            "Failed login attempt username=%r source_ip=%r reason=%s",
            username[:255],
            source_ip,
            decision.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    assert user is not None
    return user


def _require_same_origin(request: Request) -> None:
    try:
        check_same_origin(request, get_settings().CORS_ORIGINS)
    except CsrfError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"CSRF check failed: {exc}") from exc


def _session_body(issued: IssuedSession) -> dict:
    return {
        "username": issued.user.username,
        "role": issued.user.role,
        "tenant_id": issued.user.tenant_id,
        "access_expires_at": issued.access_expires_at.isoformat() + "Z",
        "refresh_expires_at": issued.refresh_expires_at.isoformat() + "Z",
    }


@router.post("/token", response_model=Token)
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Bearer token for API clients. The dashboard uses /auth/session."""
    user = await _authenticate(request, form_data.username, form_data.password, db)
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "uid": user.id, "role": user.role, "tenant_id": user.tenant_id},
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/session")
async def create_session(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    """Sign in to the dashboard: session cookies the page cannot read."""
    # Login CSRF: a foreign page must not sign the browser into its account.
    _require_same_origin(request)
    user = await _authenticate(request, form_data.username, form_data.password, db)
    issued = await start_session(db, user, request.headers.get("user-agent"))
    set_session_cookies(response, issued)
    return _session_body(issued)


@router.post("/refresh")
async def refresh_session(request: Request, db: AsyncSession = Depends(get_db)):
    _require_same_origin(request)
    token = request.cookies.get(session_cookie_names().refresh)
    try:
        if not token:
            raise SessionError("no session")
        issued = await rotate_session(db, token, request.headers.get("user-agent"))
    except SessionError as exc:
        logger.info("Session refresh refused: %s", exc)
        failed = JSONResponse({"detail": "Session expired"}, status_code=status.HTTP_401_UNAUTHORIZED)
        clear_session_cookies(failed)
        return failed
    response = JSONResponse(_session_body(issued))
    set_session_cookies(response, issued)
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    _require_same_origin(request)
    await end_session(db, request.cookies.get(session_cookie_names().refresh))
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session_cookies(response)
    return response


@router.get("/users/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user

BOOTSTRAP_TOKEN_HEADER = "X-Argus-Bootstrap-Token"


def _bootstrap_allowed(request: Request) -> bool:
    settings = get_settings()
    if settings.DEBUG:
        return True
    expected = settings.AUTH_BOOTSTRAP_TOKEN or ""
    presented = request.headers.get(BOOTSTRAP_TOKEN_HEADER, "")
    return len(expected) >= 32 and hmac.compare_digest(presented.encode(), expected.encode())


@router.post("/users", response_model=UserResponse)
async def create_user(
    user: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    # Bootstrap rule: the very first user becomes the administrator (nobody
    # else could grant the role). Whoever reaches a fresh deployment first
    # must not get it, so outside DEBUG this needs the bootstrap token (or
    # the shell: python -m app.cli.create_admin). Once any user exists, only
    # an authenticated admin may create accounts.
    user_count = (await db.execute(select(func.count(User.id)))).scalar() or 0

    if user_count == 0:
        if not _bootstrap_allowed(request):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Create the first administrator with `python -m app.cli.create_admin` on the server",
            )
        role = UserRole.ADMIN.value
        tenant_id = "1"
    else:
        if (
            current_user is None
            or not current_user.is_active
            or current_user.role != UserRole.ADMIN.value
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only administrators can create users",
            )
        role = user.role
        # New accounts always belong to the creating admin's tenant so a
        # multi-tenant deployment cannot mint users into foreign tenants.
        tenant_id = current_user.tenant_id

    valid_roles = {r.value for r in UserRole}
    if role not in valid_roles:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid role '{role}'. Allowed: {', '.join(sorted(valid_roles))}",
        )

    result = await db.execute(select(User).where(User.username == user.username))
    db_user = result.scalar_one_or_none()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")

    hashed_password = get_password_hash(user.password)
    new_user = User(
        username=user.username,
        hashed_password=hashed_password,
        role=role,
        tenant_id=tenant_id,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user
