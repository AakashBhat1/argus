"""User authentication for the parking service.

Parking stores no users or passwords. Access tokens are issued by the
surveillance service (the identity provider) as EdDSA JWTs; parking verifies
them against the provider's JWKS, fetched over the internal mTLS channel (or
read from AUTH_JWKS_FILE). Tokens are short-lived, and the tenant and role
claims drive every authorization decision here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, Request, WebSocket, status
from fastapi.security import OAuth2PasswordBearer

from app.config import get_settings
from app.mesh import OUTBOUND_SCOPES, mesh
from argus_common.keys import public_keys_from_jwks
from argus_common.tokens import JwksCache, StaticKeys, TokenError, UserClaims, UserTokenVerifier
from argus_common.web_auth import (
    CookieNames,
    CsrfError,
    cookie_names,
    http_credential,
    parse_subprotocol_token,
    websocket_credential,
)

logger = logging.getLogger(__name__)

ADMIN_ROLE = "admin"

# Declared for the OpenAPI "Authorize" flow; credentials are read from the
# request (Bearer header or the dashboard's session cookie).
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


@dataclass(frozen=True)
class Principal:
    """The authenticated user, as asserted by the identity provider."""

    username: str
    id: Optional[str]
    tenant_id: str
    role: str
    is_active: bool = True


_verifier: Optional[UserTokenVerifier] = None


async def _fetch_jwks() -> dict:
    settings = get_settings()
    async with mesh.client(
        peer="surveillance",
        base_url=_origin(settings.AUTH_JWKS_URL),
        scopes=OUTBOUND_SCOPES["surveillance"],
    ) as client:
        response = await client.get(_path(settings.AUTH_JWKS_URL))
        response.raise_for_status()
        return response.json()


def _origin(url: str) -> str:
    scheme, _, rest = url.partition("://")
    return f"{scheme}://{rest.split('/', 1)[0]}"


def _path(url: str) -> str:
    rest = url.partition("://")[2]
    return "/" + rest.split("/", 1)[1] if "/" in rest else "/"


def token_verifier() -> UserTokenVerifier:
    global _verifier
    if _verifier is None:
        settings = get_settings()
        if settings.AUTH_JWKS_FILE:
            keys = public_keys_from_jwks(json.loads(Path(settings.AUTH_JWKS_FILE).read_text()))
            _verifier = UserTokenVerifier(StaticKeys(keys), settings.AUTH_ISSUER, settings.AUTH_AUDIENCE)
        elif settings.AUTH_JWKS_URL:
            cache = JwksCache(_fetch_jwks, ttl_seconds=settings.AUTH_JWKS_TTL_SECONDS)
            _verifier = UserTokenVerifier(
                cache, settings.AUTH_ISSUER, settings.AUTH_AUDIENCE, before_verify=cache.ensure
            )
        else:
            raise RuntimeError("Configure AUTH_JWKS_URL or AUTH_JWKS_FILE to verify user tokens")
    return _verifier


def set_token_verifier(verifier: Optional[UserTokenVerifier]) -> None:
    """Install a verifier (tests) or reset to configuration (None)."""
    global _verifier
    _verifier = verifier


async def _verified_claims(token: str) -> Optional[UserClaims]:
    try:
        return await token_verifier().verify(token)
    except TokenError:
        return None
    except Exception:
        # JWKS unreachable: fail closed.
        logger.exception("User token verification failed")
        return None


def _principal(claims: UserClaims) -> Principal:
    return Principal(
        username=claims.subject,
        id=claims.user_id,
        tenant_id=claims.tenant_id,
        role=claims.role,
    )


async def principal_from_token(token: str) -> Optional[Principal]:
    claims = await _verified_claims(token)
    return _principal(claims) if claims else None


def session_cookie_names() -> CookieNames:
    settings = get_settings()
    if not settings.AUTH_COOKIE_SECURE and not settings.DEBUG:
        raise RuntimeError("AUTH_COOKIE_SECURE=false is only allowed with DEBUG")
    return cookie_names(settings.AUTH_COOKIE_SECURE)


async def get_current_active_user(
    request: Request, _bearer: Optional[str] = Depends(oauth2_scheme_optional)
) -> Principal:
    try:
        credential = http_credential(request, session_cookie_names(), get_settings().CORS_ORIGINS)
    except CsrfError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"CSRF check failed: {exc}") from exc
    principal = await principal_from_token(credential.token) if credential else None
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


async def require_admin(current_user: Principal = Depends(get_current_active_user)) -> Principal:
    if current_user.role != ADMIN_ROLE:
        raise HTTPException(status_code=403, detail="Not enough privileges")
    return current_user


websocket_subprotocol_token = parse_subprotocol_token


@dataclass(frozen=True)
class WebSocketAuth:
    principal: Principal
    subprotocol: Optional[str]
    expires_at: int


async def authenticate_websocket(websocket: WebSocket) -> Optional[WebSocketAuth]:
    """Subprotocol JWT or session cookie; foreign origins are refused."""
    credential = websocket_credential(websocket, session_cookie_names(), get_settings().CORS_ORIGINS)
    if credential is None:
        return None
    claims = await _verified_claims(credential.token)
    if claims is None:
        return None
    return WebSocketAuth(_principal(claims), credential.subprotocol, claims.expires_at)
