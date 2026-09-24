"""Service-to-service HTTP: FastAPI guards and an mTLS client.

Internal routes accept *only* a service token in ``X-Argus-Service-Token``
(never a user token), and optionally require the TLS proxy's client-cert
verification header. Outbound calls use mutual TLS pinned to the private CA
and attach a fresh single-use service token to every request.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import httpx
from fastapi import Header, HTTPException, Request, status

from argus_common.tokens import (
    ScopeError,
    ServicePrincipal,
    ServiceTokenSigner,
    ServiceTokenVerifier,
    TokenError,
)

SERVICE_TOKEN_HEADER = "X-Argus-Service-Token"
MTLS_VERIFIED_HEADER = "X-Argus-Client-Verified"


class ServiceAuth:
    """FastAPI dependency factory for internal endpoints."""

    def __init__(
        self,
        verifier_provider: Callable[[], ServiceTokenVerifier],
        require_mtls_header: Callable[[], bool] = lambda: False,
    ) -> None:
        self._verifier_provider = verifier_provider
        self._require_mtls_header = require_mtls_header

    def require(self, *scopes: str):
        async def dependency(
            request: Request,
            token: Optional[str] = Header(default=None, alias=SERVICE_TOKEN_HEADER),
        ) -> ServicePrincipal:
            if self._require_mtls_header():
                # Set by the internal TLS listener after it verified the peer's
                # client certificate (and stripped any client-supplied copy).
                if request.headers.get(MTLS_VERIFIED_HEADER) != "SUCCESS":
                    raise HTTPException(status.HTTP_403_FORBIDDEN, "mutual TLS required")
            if not token:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "service token required")
            try:
                return self._verifier_provider().verify(token, scopes)
            except ScopeError as exc:
                raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
            except TokenError as exc:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid service token") from exc

        return dependency


@dataclass(frozen=True)
class MtlsConfig:
    ca_file: Optional[str] = None
    cert_file: Optional[str] = None
    key_file: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.ca_file and self.cert_file and self.key_file)


def mtls_context(config: MtlsConfig) -> ssl.SSLContext:
    """Client TLS context that trusts only the private CA and presents our cert."""
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=config.ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config.cert_file, config.key_file)
    return context


def internal_client(
    *,
    base_url: str,
    signer: ServiceTokenSigner,
    audience: str,
    scopes: Iterable[str],
    mtls: MtlsConfig,
    timeout: float = 5.0,
    allow_plaintext: bool = False,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> httpx.AsyncClient:
    """HTTP client for calling a peer service's internal API.

    Plain HTTP is refused unless ``allow_plaintext`` (single-host development
    on a private Docker network, or tests with an in-process transport).
    """
    scope_list = tuple(scopes)
    if base_url.startswith("https://"):
        if not mtls.enabled:
            raise ValueError("mTLS certificate, key and CA are required for internal HTTPS calls")
        verify: ssl.SSLContext | bool = mtls_context(mtls)
    elif allow_plaintext:
        verify = True
    else:
        raise ValueError(f"Refusing non-TLS internal endpoint {base_url!r}")

    async def attach_token(request: httpx.Request) -> None:
        # Service tokens are single-use (replay cache), so every request
        # carries a freshly minted one.
        request.headers[SERVICE_TOKEN_HEADER] = signer.token_for(audience, scope_list)

    return httpx.AsyncClient(
        base_url=base_url,
        verify=verify,
        timeout=timeout,
        transport=transport,
        event_hooks={"request": [attach_token]},
        follow_redirects=False,
    )
