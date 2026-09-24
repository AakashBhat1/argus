import logging
import os
import threading
from datetime import timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from argus_common.keys import KeyConfigError, SigningKey, jwks, key_id_for, load_public_pem, load_signing_key
from argus_common.tokens import StaticKeys, TokenError, UserClaims, UserTokenIssuer, UserTokenVerifier
from app.database import get_db, get_session_factory
from app.models import User, UserRole

from dotenv import load_dotenv

from app.config import env_file_path, get_settings

_env_file = env_file_path()
if _env_file:
    load_dotenv(_env_file)

logger = logging.getLogger(__name__)

_INSECURE_DEFAULT = "12345678901234567890123456789012"
# Known placeholder secrets that must never be used in production.
_INSECURE_DEFAULTS = {
    _INSECURE_DEFAULT,
    "super-secret-key-change-in-production",
    "change-me-in-production",
}
SECRET_KEY = os.getenv("SECRET_KEY", _INSECURE_DEFAULT)

_DEFAULT_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
try:
    ACCESS_TOKEN_EXPIRE_MINUTES = int(
        os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", str(_DEFAULT_TOKEN_EXPIRE_MINUTES))
    )
except ValueError:
    ACCESS_TOKEN_EXPIRE_MINUTES = _DEFAULT_TOKEN_EXPIRE_MINUTES
if ACCESS_TOKEN_EXPIRE_MINUTES <= 0:
    ACCESS_TOKEN_EXPIRE_MINUTES = _DEFAULT_TOKEN_EXPIRE_MINUTES

# Validate SECRET_KEY strength in production mode
_debug_mode = os.getenv("DEBUG", "false").strip().lower() in (
    "1", "true", "yes", "on", "debug", "development", "dev",
)
if not _debug_mode:
    if SECRET_KEY in _INSECURE_DEFAULTS:
        raise RuntimeError(
            "SECRET_KEY is set to the insecure default. "
            "Set a strong SECRET_KEY environment variable for production."
        )
    if len(SECRET_KEY) < 32:
        raise RuntimeError(
            "SECRET_KEY must be at least 32 characters long for production use."
        )

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

# -- token keys ---------------------------------------------------------------
#
# Access tokens are EdDSA (Ed25519) JWTs. Only this service holds the private
# key; other services verify with the public JWKS at /.well-known/jwks.json,
# so no secret is shared across machines. AUTH_PREVIOUS_PUBLIC_KEYS_DIR holds
# public PEMs of retired keys that remain valid during a rotation window.

_keys_lock = threading.Lock()
_signing_key: Optional[SigningKey] = None
_verification_keys: Optional[dict] = None


def _debug_mode() -> bool:
    return bool(get_settings().DEBUG)


def signing_key() -> SigningKey:
    global _signing_key
    if _signing_key is None:
        with _keys_lock:
            if _signing_key is None:
                settings = get_settings()
                try:
                    _signing_key = load_signing_key(
                        path=settings.AUTH_SIGNING_KEY_FILE,
                        allow_ephemeral=_debug_mode(),
                    )
                except KeyConfigError as exc:
                    raise RuntimeError(
                        f"Auth signing key unavailable: {exc}. Set AUTH_SIGNING_KEY_FILE "
                        "(Ed25519 PEM, see deploy/pki.sh)."
                    ) from exc
                if not settings.AUTH_SIGNING_KEY_FILE:
                    logger.warning(
                        "DEBUG: using an ephemeral auth signing key; tokens will not "
                        "survive a restart and peers cannot pin it"
                    )
    return _signing_key


def verification_keys() -> dict:
    """``{kid: public_key}`` for the active key plus rotation keys."""
    global _verification_keys
    if _verification_keys is None:
        active = signing_key()
        keys = {active.kid: active.public_key}
        directory = get_settings().AUTH_PREVIOUS_PUBLIC_KEYS_DIR
        if directory:
            for pem_file in sorted(Path(directory).glob("*.pem")):
                public = load_public_pem(pem_file.read_bytes())
                keys[key_id_for(public)] = public
        _verification_keys = keys
    return _verification_keys


def reset_key_cache() -> None:
    """Forget loaded keys (tests and key rotation)."""
    global _signing_key, _verification_keys
    with _keys_lock:
        _signing_key = None
        _verification_keys = None


def token_issuer() -> UserTokenIssuer:
    settings = get_settings()
    return UserTokenIssuer(signing_key(), settings.AUTH_ISSUER, settings.AUTH_AUDIENCE)


def token_verifier() -> UserTokenVerifier:
    settings = get_settings()
    return UserTokenVerifier(
        StaticKeys(verification_keys()), settings.AUTH_ISSUER, settings.AUTH_AUDIENCE
    )


def jwks_document() -> dict:
    return jwks((key, kid) for kid, key in verification_keys().items())


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Issue an access token for ``data['sub']`` (tenant and role required)."""
    ttl = expires_delta if expires_delta is not None else timedelta(minutes=15)
    tenant_id = data.get("tenant_id")
    if not tenant_id:
        raise ValueError("access tokens must carry tenant_id")
    return token_issuer().issue(
        subject=str(data["sub"]),
        tenant_id=str(tenant_id),
        role=str(data.get("role") or UserRole.OPERATOR.value),
        user_id=data.get("uid"),
        ttl_seconds=int(ttl.total_seconds()),
    )


def decode_access_token(token: str) -> Optional[UserClaims]:
    try:
        return token_verifier().verify_sync(token)
    except TokenError:
        return None


async def _active_user_for(claims: UserClaims, session: AsyncSession) -> Optional[User]:
    """The DB is authoritative: the user must exist, be active and still
    belong to the tenant the token was issued for."""
    result = await session.execute(select(User).where(User.username == claims.subject))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or user.tenant_id != claims.tenant_id:
        return None
    return user


WS_AUTH_SUBPROTOCOL = "argus-jwt"


def websocket_subprotocol_token(header: str) -> Optional[str]:
    """Extract the JWT from ``Sec-WebSocket-Protocol: argus-jwt, <token>``.

    The marker is located by value rather than position so an intermediary
    that reorders the list cannot break authentication; anything other than
    exactly the marker plus one token is rejected.
    """
    offered = [value.strip() for value in (header or "").split(",") if value.strip()]
    if len(offered) != 2 or offered.count(WS_AUTH_SUBPROTOCOL) != 1:
        return None
    token = offered[1 - offered.index(WS_AUTH_SUBPROTOCOL)]
    return token or None


async def authenticate_websocket(websocket: WebSocket) -> Optional[User]:
    """Authenticate an active user from a WebSocket subprotocol JWT."""
    token = websocket_subprotocol_token(websocket.headers.get("sec-websocket-protocol", ""))
    if not token:
        return None

    claims = decode_access_token(token)
    if claims is None:
        return None

    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            user = await _active_user_for(claims, session)
    except SQLAlchemyError:
        logger.exception("WebSocket authentication database query failed")
        return None
    except RuntimeError:
        logger.exception("WebSocket authentication session is unavailable")
        return None

    return user

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    claims = decode_access_token(token)
    if claims is None:
        raise credentials_exception
    user = await _active_user_for(claims, db)
    if user is None:
        raise credentials_exception
    return user

async def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme_optional),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Resolve the current user if a valid bearer token was sent, else None.

    Used by endpoints that behave differently for anonymous callers
    (e.g. first-user bootstrap on /auth/users) instead of hard-failing.
    """
    if not token:
        return None
    claims = decode_access_token(token)
    if claims is None:
        return None
    return await _active_user_for(claims, db)

async def get_current_active_user(current_user: User = Depends(get_current_user)):
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

async def require_admin(current_user: User = Depends(get_current_active_user)):
    if current_user.role != UserRole.ADMIN.value:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    return current_user
