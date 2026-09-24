"""How user-facing endpoints receive access tokens, and CSRF defences.

Two ways to present an access token:

- API clients send ``Authorization: Bearer <jwt>`` (WebSockets:
  ``Sec-WebSocket-Protocol: argus-jwt, <jwt>``). Browsers never attach these
  on their own, so they carry no CSRF risk.
- The dashboard holds the token in an httpOnly cookie that page scripts
  cannot read, so an XSS bug cannot exfiltrate it. Browsers do attach cookies
  on their own, so a cookie-authenticated request must show it comes from
  our origin: state-changing requests need the ``X-Argus-CSRF`` header (a
  cross-site page cannot set custom headers without a CORS grant) and, when
  the browser sends one, an allowed ``Origin``. WebSocket handshakes carrying
  an ``Origin`` must come from an allowed origin (cross-site WebSocket
  hijacking).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Iterable, Literal, Optional
from urllib.parse import urlsplit

from starlette.requests import HTTPConnection
from starlette.websockets import WebSocket

ACCESS_COOKIE = "__Host-argus_at"
REFRESH_COOKIE = "__Secure-argus_rt"
# Development over plain http on a LAN address, where browsers refuse the
# prefixed (Secure-only) names. Services only accept these in DEBUG.
INSECURE_ACCESS_COOKIE = "argus_at"
INSECURE_REFRESH_COOKIE = "argus_rt"
# The refresh cookie only travels to the session endpoints.
REFRESH_COOKIE_PATH = "/api/v1/auth"
CSRF_HEADER = "X-Argus-CSRF"
WS_AUTH_SUBPROTOCOL = "argus-jwt"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Close code for "authenticate again": the dashboard refreshes its session
# and reconnects.
WS_AUTH_CLOSE_CODE = 4001

_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


class CsrfError(Exception):
    """A cookie-authenticated request failed the same-origin checks."""


@dataclass(frozen=True)
class CookieNames:
    access: str
    refresh: str
    secure: bool


def cookie_names(secure: bool = True) -> CookieNames:
    if secure:
        return CookieNames(ACCESS_COOKIE, REFRESH_COOKIE, True)
    return CookieNames(INSECURE_ACCESS_COOKIE, INSECURE_REFRESH_COOKIE, False)


@dataclass(frozen=True)
class Credential:
    token: str
    source: Literal["bearer", "cookie", "subprotocol"]
    # Echoed when accepting a WebSocket that offered one.
    subprotocol: Optional[str] = None


def _origin_key(origin: str) -> Optional[tuple[str, str, int]]:
    try:
        parts = urlsplit(origin.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not parts.hostname or parts.path not in ("", "/"):
        return None
    return scheme, parts.hostname.lower(), port or _DEFAULT_PORTS[scheme]


def origin_allowed(origin: Optional[str], host: Optional[str], allowed: Iterable[str] = ()) -> bool:
    """Same origin as the ``Host`` the browser addressed, or explicitly allowed.

    ``host`` is the request's Host header; the edge proxy forwards it
    unchanged, and a cross-site page cannot choose it.
    """
    if not origin or origin == "null":
        return False
    key = _origin_key(origin)
    if key is None:
        return False
    scheme, hostname, port = key
    if host:
        request_host = _origin_key(f"{scheme}://{host}")
        if request_host == key:
            return True
    return any(_origin_key(entry) == key for entry in allowed)


def check_same_origin(conn: HTTPConnection, allowed: Iterable[str] = ()) -> None:
    """Raise ``CsrfError`` unless a cookie-authenticated request is ours."""
    method = conn.scope.get("method", "GET")
    if method in SAFE_METHODS:
        return
    if conn.headers.get(CSRF_HEADER) != "1":
        raise CsrfError("missing CSRF header")
    # Browsers send Origin on every cross-origin and every non-GET fetch; a
    # request with the custom header and no Origin is same-origin from an
    # old browser or a non-browser client.
    origin = conn.headers.get("origin")
    if origin is not None and not origin_allowed(origin, conn.headers.get("host"), allowed):
        raise CsrfError("origin not allowed")


def bearer_token(conn: HTTPConnection) -> Optional[str]:
    scheme, _, token = conn.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def http_credential(conn: HTTPConnection, names: CookieNames, allowed_origins: Iterable[str] = ()) -> Optional[Credential]:
    """The request's access token: a Bearer header wins over the cookie."""
    token = bearer_token(conn)
    if token:
        return Credential(token, "bearer")
    token = conn.cookies.get(names.access)
    if not token:
        return None
    check_same_origin(conn, allowed_origins)
    return Credential(token, "cookie")


def parse_subprotocol_token(header: str) -> Optional[str]:
    """Extract the JWT from ``Sec-WebSocket-Protocol: argus-jwt, <token>``.

    The marker is located by value so a reordering intermediary cannot break
    authentication; anything but exactly the marker plus one token is refused.
    """
    offered = [value.strip() for value in (header or "").split(",") if value.strip()]
    if len(offered) != 2 or offered.count(WS_AUTH_SUBPROTOCOL) != 1:
        return None
    return offered[1 - offered.index(WS_AUTH_SUBPROTOCOL)] or None


def websocket_credential(
    conn: HTTPConnection, names: CookieNames, allowed_origins: Iterable[str] = ()
) -> Optional[Credential]:
    """The handshake's access token, or None (including foreign origins)."""
    origin = conn.headers.get("origin")
    if origin is not None and not origin_allowed(origin, conn.headers.get("host"), allowed_origins):
        return None
    token = parse_subprotocol_token(conn.headers.get("sec-websocket-protocol", ""))
    if token:
        return Credential(token, "subprotocol", WS_AUTH_SUBPROTOCOL)
    token = conn.cookies.get(names.access)
    if token:
        return Credential(token, "cookie")
    return None


async def hold_until_expiry(websocket: WebSocket, expires_at: float) -> None:
    """Drain client messages until the peer disconnects or the access token
    expires, then close with ``WS_AUTH_CLOSE_CODE``.

    A socket must not outlive the token that opened it, or a revoked or
    downgraded user would keep receiving the feed. ``WebSocketDisconnect``
    propagates to the caller.
    """
    while True:
        remaining = expires_at - time.time()
        if remaining <= 0:
            await websocket.close(code=WS_AUTH_CLOSE_CODE)
            return
        try:
            await asyncio.wait_for(websocket.receive_text(), timeout=remaining)
        except asyncio.TimeoutError:
            continue
