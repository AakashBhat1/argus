"""User and service tokens (EdDSA JWTs).

User access tokens are issued by the surveillance service (the identity
provider) and verified by every service from its public JWKS.

Service tokens authenticate one service to another on internal routes, in
addition to mutual TLS. They are short-lived, audience-bound, single-use
(``jti`` replay cache) and carry scopes; the *receiver* decides which scopes
each peer may hold, so a compromised peer cannot grant itself more.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Mapping, Optional, Protocol

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from argus_common.keys import SigningKey, public_keys_from_jwks

ALGORITHM = "EdDSA"
LEEWAY_SECONDS = 30
MAX_SERVICE_TOKEN_TTL = 120


class TokenError(Exception):
    """Token is missing, malformed, expired or not acceptable here."""


class ScopeError(TokenError):
    """Token is valid but lacks a required scope."""


# -- key resolution ----------------------------------------------------------


class KeyResolver(Protocol):
    def get(self, kid: str) -> Optional[Ed25519PublicKey]: ...


@dataclass
class StaticKeys:
    keys: dict[str, Ed25519PublicKey] = field(default_factory=dict)

    def get(self, kid: str) -> Optional[Ed25519PublicKey]:
        return self.keys.get(kid)


class JwksCache:
    """JWKS fetched from the identity provider, cached and refreshed.

    ``fetch`` is an async callable returning the JWKS document (typically an
    mTLS GET of ``/.well-known/jwks.json``). Unknown key ids trigger at most
    one refresh per ``min_refresh_interval`` so a flood of forged ``kid``
    values cannot turn into a flood of fetches.
    """

    def __init__(
        self,
        fetch: Callable[[], Awaitable[dict]],
        ttl_seconds: float = 300.0,
        min_refresh_interval: float = 30.0,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._min_interval = min_refresh_interval
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fetched_at = 0.0
        self._attempted_at = 0.0
        self._lock = asyncio.Lock()

    def get(self, kid: str) -> Optional[Ed25519PublicKey]:
        return self._keys.get(kid)

    @property
    def stale(self) -> bool:
        return time.monotonic() - self._fetched_at > self._ttl

    async def ensure(self, kid: Optional[str] = None) -> None:
        if not self.stale and (kid is None or kid in self._keys):
            return
        async with self._lock:
            if not self.stale and (kid is None or kid in self._keys):
                return
            now = time.monotonic()
            if now - self._attempted_at < self._min_interval and self._keys:
                return
            self._attempted_at = now
            document = await self._fetch()
            keys = public_keys_from_jwks(document)
            if keys:
                self._keys = keys
                self._fetched_at = time.monotonic()


# -- user tokens ---------------------------------------------------------------


@dataclass(frozen=True)
class UserClaims:
    subject: str
    user_id: Optional[str]
    tenant_id: str
    role: str
    token_id: str
    expires_at: int
    raw: Mapping


class UserTokenIssuer:
    def __init__(self, key: SigningKey, issuer: str, audience: str) -> None:
        self._key = key
        self._issuer = issuer
        self._audience = audience

    @property
    def key(self) -> SigningKey:
        return self._key

    def issue(
        self,
        *,
        subject: str,
        tenant_id: str,
        role: str,
        ttl_seconds: int,
        user_id: Optional[str] = None,
        token_type: str = "access",
        extra: Optional[Mapping] = None,
    ) -> str:
        now = int(time.time())
        payload = {
            **(dict(extra) if extra else {}),
            "iss": self._issuer,
            "aud": self._audience,
            "sub": subject,
            "uid": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "typ": token_type,
            "iat": now,
            "nbf": now,
            "exp": now + int(ttl_seconds),
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(
            payload, self._key.private_key, algorithm=ALGORITHM, headers={"kid": self._key.kid}
        )


def _decode(token: str, resolver: KeyResolver, *, audience: str, issuer: Optional[str]) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError("malformed token") from exc
    if header.get("alg") != ALGORITHM:
        raise TokenError("unexpected algorithm")
    kid = header.get("kid")
    if not isinstance(kid, str):
        raise TokenError("missing key id")
    key = resolver.get(kid)
    if key is None:
        raise TokenError("unknown signing key")
    options = {"require": ["exp", "iat", "sub", "aud", "iss", "jti"]}
    try:
        return jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options=options,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc


class UserTokenVerifier:
    def __init__(
        self,
        resolver: KeyResolver,
        issuer: str,
        audience: str,
        before_verify: Optional[Callable[[Optional[str]], Awaitable[None]]] = None,
    ) -> None:
        self._resolver = resolver
        self._issuer = issuer
        self._audience = audience
        self._before_verify = before_verify

    def verify_sync(self, token: str, token_type: str = "access") -> UserClaims:
        claims = _decode(token, self._resolver, audience=self._audience, issuer=self._issuer)
        return self._claims(claims, token_type)

    async def verify(self, token: str, token_type: str = "access") -> UserClaims:
        if self._before_verify is not None:
            try:
                kid = jwt.get_unverified_header(token).get("kid")
            except jwt.PyJWTError as exc:
                raise TokenError("malformed token") from exc
            await self._before_verify(kid if isinstance(kid, str) else None)
        return self.verify_sync(token, token_type)

    @staticmethod
    def _claims(claims: dict, token_type: str) -> UserClaims:
        if claims.get("typ") != token_type:
            raise TokenError("wrong token type")
        tenant_id = claims.get("tenant_id")
        role = claims.get("role")
        if not isinstance(tenant_id, str) or not tenant_id or not isinstance(role, str):
            raise TokenError("missing tenant or role")
        return UserClaims(
            subject=str(claims["sub"]),
            user_id=claims.get("uid"),
            tenant_id=tenant_id,
            role=role,
            token_id=str(claims["jti"]),
            expires_at=int(claims["exp"]),
            raw=claims,
        )


# -- service tokens ------------------------------------------------------------


class ReplayCache:
    """Remembers token ids until they expire; rejects reuse.

    Process-local: with mTLS in front and a 60 s lifetime this bounds replay
    to a single worker; deployments running several workers per service can
    swap in a shared implementation with the same interface.
    """

    def __init__(self, max_entries: int = 100_000) -> None:
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._max = max_entries

    def check_and_remember(self, jti: str, expires_at: float) -> bool:
        now = time.time()
        with self._lock:
            if len(self._seen) > self._max or len(self._seen) % 1024 == 0:
                self._seen = {k: v for k, v in self._seen.items() if v > now}
            if jti in self._seen and self._seen[jti] > now:
                return False
            self._seen[jti] = expires_at + LEEWAY_SECONDS
            return True


@dataclass(frozen=True)
class ServicePrincipal:
    service: str
    scopes: frozenset[str]
    token_id: str


@dataclass(frozen=True)
class PeerPolicy:
    """What the receiving service accepts from one peer."""

    keys: dict[str, Ed25519PublicKey]
    allowed_scopes: frozenset[str]


class ServiceTokenSigner:
    def __init__(self, service_name: str, key: SigningKey, ttl_seconds: int = 60) -> None:
        if ttl_seconds > MAX_SERVICE_TOKEN_TTL:
            raise ValueError("service tokens must be short-lived")
        self._service = service_name
        self._key = key
        self._ttl = ttl_seconds

    @property
    def service(self) -> str:
        return self._service

    def token_for(self, audience: str, scopes: Iterable[str]) -> str:
        now = int(time.time())
        payload = {
            "iss": self._service,
            "sub": self._service,
            "aud": audience,
            "scope": " ".join(sorted(set(scopes))),
            "typ": "service",
            "iat": now,
            "nbf": now,
            "exp": now + self._ttl,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(
            payload, self._key.private_key, algorithm=ALGORITHM, headers={"kid": self._key.kid}
        )


class ServiceTokenVerifier:
    def __init__(
        self,
        service_name: str,
        peers: Mapping[str, PeerPolicy],
        replay_cache: Optional[ReplayCache] = None,
    ) -> None:
        self._service = service_name
        self._peers = dict(peers)
        self._replay = replay_cache or ReplayCache()

    def verify(self, token: str, required_scopes: Iterable[str] = ()) -> ServicePrincipal:
        try:
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise TokenError("malformed token") from exc
        issuer = unverified.get("iss")
        policy = self._peers.get(issuer) if isinstance(issuer, str) else None
        if policy is None:
            raise TokenError("unknown peer service")
        claims = _decode(
            token, StaticKeys(policy.keys), audience=self._service, issuer=issuer
        )
        if claims.get("typ") != "service" or claims.get("sub") != issuer:
            raise TokenError("not a service token")
        if int(claims["exp"]) - int(claims["iat"]) > MAX_SERVICE_TOKEN_TTL:
            raise TokenError("service token lifetime too long")
        scopes = frozenset(str(claims.get("scope", "")).split())
        if not scopes <= policy.allowed_scopes:
            raise ScopeError("token carries scopes this peer is not granted")
        missing = set(required_scopes) - scopes
        if missing:
            raise ScopeError(f"missing scope: {', '.join(sorted(missing))}")
        if not self._replay.check_and_remember(str(claims["jti"]), float(claims["exp"])):
            raise TokenError("token replayed")
        return ServicePrincipal(service=issuer, scopes=scopes, token_id=str(claims["jti"]))
