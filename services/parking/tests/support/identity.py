"""A stand-in identity provider for parking tests.

Parking stores no users: principals come from access tokens issued by the
surveillance service. Tests mint such tokens with a throwaway Ed25519 key
whose public half parking is configured to trust.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from argus_common.keys import SigningKey
from argus_common.tokens import StaticKeys, UserTokenIssuer, UserTokenVerifier

ISSUER = "argus-surveillance"
AUDIENCE = "argus"
IDP_KEY = SigningKey.generate()


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    OPERATOR = "operator"


@dataclass
class User:
    """Shape-compatible with the old ORM user used by these tests."""

    username: str
    role: str = UserRole.OPERATOR.value
    tenant_id: str = "tenant-1"
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_active: bool = True
    hashed_password: Optional[str] = None


def verifier() -> UserTokenVerifier:
    return UserTokenVerifier(StaticKeys({IDP_KEY.kid: IDP_KEY.public_key}), ISSUER, AUDIENCE)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    ttl = expires_delta if expires_delta is not None else timedelta(minutes=15)
    return UserTokenIssuer(IDP_KEY, ISSUER, AUDIENCE).issue(
        subject=str(data["sub"]),
        tenant_id=str(data["tenant_id"]),
        role=str(data.get("role") or UserRole.OPERATOR.value),
        user_id=data.get("uid"),
        ttl_seconds=int(ttl.total_seconds()),
    )


def token_for(user: User, minutes: int = 30) -> str:
    return create_access_token(
        {"sub": user.username, "role": user.role, "tenant_id": user.tenant_id, "uid": user.id},
        timedelta(minutes=minutes),
    )
