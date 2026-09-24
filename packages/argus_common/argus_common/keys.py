"""Ed25519 signing keys and JWKS helpers.

Every Argus service signs tokens with its own Ed25519 private key. Peers only
ever hold public keys (published as a JWKS or distributed as PEM files), so no
secret is shared between machines and a compromised verifier cannot mint
tokens.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class KeyConfigError(RuntimeError):
    """Raised when key material is missing or malformed."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_key_bytes(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id_for(key: Ed25519PublicKey) -> str:
    """Stable key id: RFC 7638-style thumbprint of the public key."""
    digest = hashlib.sha256(public_key_bytes(key)).digest()
    return _b64url(digest)[:16]


@dataclass(frozen=True)
class SigningKey:
    private_key: Ed25519PrivateKey
    kid: str

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    @classmethod
    def generate(cls) -> "SigningKey":
        private = Ed25519PrivateKey.generate()
        return cls(private, key_id_for(private.public_key()))

    @classmethod
    def from_pem(cls, pem: bytes, kid: Optional[str] = None) -> "SigningKey":
        try:
            private = serialization.load_pem_private_key(pem, password=None)
        except (ValueError, TypeError) as exc:
            raise KeyConfigError("Signing key is not a valid unencrypted PEM private key") from exc
        if not isinstance(private, Ed25519PrivateKey):
            raise KeyConfigError("Signing key must be an Ed25519 private key")
        return cls(private, kid or key_id_for(private.public_key()))

    def private_pem(self) -> bytes:
        return self.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def public_pem(self) -> bytes:
        return public_pem(self.public_key)


def public_pem(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def load_public_pem(pem: bytes) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(pem)
    except (ValueError, TypeError) as exc:
        raise KeyConfigError("Not a valid PEM public key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise KeyConfigError("Public key must be Ed25519")
    return key


def load_signing_key(
    *,
    path: Optional[str] = None,
    pem: Optional[str] = None,
    kid: Optional[str] = None,
    allow_ephemeral: bool = False,
) -> SigningKey:
    """Load the service signing key from a file or inline PEM.

    ``allow_ephemeral`` generates a throwaway key when nothing is configured;
    only development and tests may use it (tokens die with the process).
    """
    if path:
        file = Path(path)
        if not file.is_file():
            raise KeyConfigError(f"Signing key file not found: {path}")
        _check_permissions(file)
        return SigningKey.from_pem(file.read_bytes(), kid)
    if pem:
        return SigningKey.from_pem(pem.encode("utf-8"), kid)
    if allow_ephemeral:
        return SigningKey.generate()
    raise KeyConfigError("No signing key configured")


def _check_permissions(file: Path) -> None:
    """Refuse private keys readable by group/other on POSIX."""
    if os.name != "posix":
        return
    mode = file.stat().st_mode & 0o777
    if mode & 0o077:
        raise KeyConfigError(
            f"Private key {file} is accessible by group/other (mode {mode:o}); chmod 600 it"
        )


def jwk(key: Ed25519PublicKey, kid: Optional[str] = None) -> dict:
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": _b64url(public_key_bytes(key)),
        "kid": kid or key_id_for(key),
        "alg": "EdDSA",
        "use": "sig",
    }


def jwks(keys: Iterable[tuple[Ed25519PublicKey, str]]) -> dict:
    return {"keys": [jwk(key, kid) for key, kid in keys]}


def public_keys_from_jwks(document: dict) -> dict[str, Ed25519PublicKey]:
    """Parse a JWKS document into ``{kid: key}`` (Ed25519 keys only)."""
    keys: dict[str, Ed25519PublicKey] = {}
    for entry in document.get("keys", []) if isinstance(document, dict) else []:
        if not isinstance(entry, dict):
            continue
        if entry.get("kty") != "OKP" or entry.get("crv") != "Ed25519":
            continue
        kid, x = entry.get("kid"), entry.get("x")
        if not isinstance(kid, str) or not isinstance(x, str):
            continue
        try:
            keys[kid] = Ed25519PublicKey.from_public_bytes(_b64url_decode(x))
        except ValueError:
            continue
    return keys
