"""Authenticated encryption for secrets kept in service databases.

Camera stream URLs carry the camera's username and password; a database dump
or backup must not hand them out. Values are sealed with AES-256-GCM:

    enc:v1:<key id>:<base64url(nonce | ciphertext | tag)>

- The key id names the key, so keys rotate: new writes use the active key,
  retired keys stay readable until every value has been re-sealed.
- Associated data binds a ciphertext to where it belongs (service + column):
  a value copied elsewhere fails to open instead of decrypting.
- Keys are 32 random bytes from a file readable only by the service
  (deploy/pki.sh creates them); nothing is derived from passwords.

``SealedString`` is a SQLAlchemy column type that seals on write and opens on
read, so application code keeps working with plaintext.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from argus_common.keys import KeyConfigError, _check_permissions

PREFIX = "enc:v1:"
KEY_BYTES = 32
_NONCE_BYTES = 12


class SecretBoxError(Exception):
    """A sealed value cannot be opened (wrong key, wrong place, tampered)."""


def key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class SecretBox:
    keys: dict[str, bytes]
    active: str
    _ciphers: dict[str, AESGCM] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.active not in self.keys:
            raise KeyConfigError("active key missing from the key set")
        for kid, key in self.keys.items():
            if len(key) != KEY_BYTES:
                raise KeyConfigError(f"key {kid} must be {KEY_BYTES} bytes")
            self._ciphers[kid] = AESGCM(key)

    @classmethod
    def from_key(cls, key: bytes, previous: tuple[bytes, ...] = ()) -> "SecretBox":
        keys = {key_id(k): k for k in previous}
        keys[key_id(key)] = key
        return cls(keys, key_id(key))

    @staticmethod
    def is_sealed(value: object) -> bool:
        return isinstance(value, str) and value.startswith(PREFIX)

    def seal(self, plaintext: str, aad: str) -> str:
        nonce = os.urandom(_NONCE_BYTES)
        sealed = self._ciphers[self.active].encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))
        return f"{PREFIX}{self.active}:{_b64encode(nonce + sealed)}"

    def open(self, token: str, aad: str) -> str:
        if not self.is_sealed(token):
            raise SecretBoxError("not a sealed value")
        kid, _, payload = token[len(PREFIX):].partition(":")
        cipher = self._ciphers.get(kid)
        if cipher is None:
            raise SecretBoxError(f"no key {kid!r} to open this value")
        try:
            raw = _b64decode(payload)
            return cipher.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], aad.encode("utf-8")).decode("utf-8")
        except (InvalidTag, ValueError, binascii.Error) as exc:
            raise SecretBoxError("sealed value failed authentication") from exc

    def needs_reseal(self, token: str) -> bool:
        """True for plaintext, or a value sealed with a retired key."""
        return not self.is_sealed(token) or not token.startswith(f"{PREFIX}{self.active}:")


def read_key_file(path: str) -> bytes:
    """A 32-byte key stored raw, base64 or hex; refused if group/other can read it."""
    file = Path(path)
    if not file.is_file():
        raise KeyConfigError(f"Key file not found: {path}")
    _check_permissions(file)
    data = file.read_bytes()
    if len(data) == KEY_BYTES:
        return data
    text = data.decode("ascii", errors="ignore").strip()
    for decode in (base64.b64decode, base64.urlsafe_b64decode, bytes.fromhex):
        try:
            key = decode(text)
        except (ValueError, binascii.Error):
            continue
        if len(key) == KEY_BYTES:
            return key
    raise KeyConfigError(f"{path} does not hold a {KEY_BYTES}-byte key (raw, base64 or hex)")


def load_secret_box(key_file: str, previous_keys_dir: Optional[str] = None) -> SecretBox:
    previous: list[bytes] = []
    if previous_keys_dir:
        previous = [read_key_file(str(p)) for p in sorted(Path(previous_keys_dir).glob("*.key"))]
    return SecretBox.from_key(read_key_file(key_file), tuple(previous))


class SealedString(TypeDecorator):
    """Text column sealed with the box ``box_provider`` returns.

    The provider returns None only where plaintext storage is acceptable
    (development); values written then stay readable and are sealed once a
    key is configured (see ``SecretBox.needs_reseal``).
    """

    impl = Text
    cache_ok = True

    def __init__(self, box_provider: Callable[[], Optional[SecretBox]], aad: str) -> None:
        super().__init__()
        self._box_provider = box_provider
        self.aad = aad

    def process_bind_param(self, value, dialect):
        if value is None or SecretBox.is_sealed(value):
            return value
        box = self._box_provider()
        return box.seal(value, self.aad) if box else value

    def process_result_value(self, value, dialect):
        if value is None or not SecretBox.is_sealed(value):
            return value
        box = self._box_provider()
        if box is None:
            raise SecretBoxError("sealed value found but no key is configured")
        return box.open(value, self.aad)
