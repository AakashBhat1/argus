import ipaddress
import logging
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, status
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
    verify_password,
    require_admin
)
from app.services.login_attempts import login_attempt_limiter

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)
DUMMY_PASSWORD_HASH = get_password_hash("argus-dummy-password-that-never-authenticates")


def _source_ip(request: Request) -> str:
    direct_ip = request.client.host if request.client else "unknown"
    forwarded_ip = request.headers.get("x-real-ip")
    if not forwarded_ip:
        return direct_ip

    try:
        direct_address = ipaddress.ip_address(direct_ip)
        forwarded_address = ipaddress.ip_address(forwarded_ip.strip())
    except ValueError:
        return direct_ip

    # The deployed backend is reachable through the local/private Nginx proxy,
    # which overwrites X-Real-IP. Never trust that header from a public peer.
    if direct_address.is_loopback or direct_address.is_private:
        return str(forwarded_address)
    return direct_ip

@router.post("/token", response_model=Token)
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()

    password_hash = user.hashed_password if user is not None else DUMMY_PASSWORD_HASH
    password_matches = verify_password(form_data.password, password_hash)
    credentials_valid = user is not None and password_matches
    source_ip = _source_ip(request)
    decision = login_attempt_limiter.evaluate(
        source_ip=source_ip,
        username=form_data.username,
        credentials_valid=credentials_valid,
    )

    if not decision.allowed:
        logger.warning(
            "Failed login attempt username=%r source_ip=%r reason=%s",
            form_data.username[:255],
            source_ip,
            decision.reason,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    assert user is not None
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role, "tenant_id": user.tenant_id},
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/users/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    return current_user

@router.post("/users", response_model=UserResponse)
async def create_user(
    user: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    # Bootstrap rule: the very first user may be created anonymously and is
    # forced to admin (there is no one else who could grant the role). Once
    # any user exists, only an authenticated admin may create accounts.
    user_count = (await db.execute(select(func.count(User.id)))).scalar() or 0

    if user_count == 0:
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
