"""Sealing secrets at rest: round trip, tamper, wrong place, rotation, column type."""

from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import Column, Integer, create_engine, select, text
from sqlalchemy.orm import DeclarativeBase, Session

from argus_common.keys import KeyConfigError
from argus_common.secretbox import (
    PREFIX,
    SealedString,
    SecretBox,
    SecretBoxError,
    key_id,
    load_secret_box,
    read_key_file,
)

URL = "rtsp://admin:S3cret!@192.0.2.10:554/Streaming/Channels/101"
AAD = "argus-test:cameras.stream_url"


def test_round_trip_hides_the_plaintext():
    box = SecretBox.from_key(os.urandom(32))
    sealed = box.seal(URL, AAD)
    assert sealed.startswith(PREFIX) and "S3cret" not in sealed and "admin" not in sealed
    assert box.open(sealed, AAD) == URL
    assert box.seal(URL, AAD) != sealed  # fresh nonce every time


def test_tampering_and_misplacement_fail_closed():
    box = SecretBox.from_key(os.urandom(32))
    sealed = box.seal(URL, AAD)
    flipped = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]
    with pytest.raises(SecretBoxError):
        box.open(flipped, AAD)
    with pytest.raises(SecretBoxError):
        box.open(sealed, "argus-other:cameras.stream_url")
    with pytest.raises(SecretBoxError):
        SecretBox.from_key(os.urandom(32)).open(sealed, AAD)
    with pytest.raises(SecretBoxError):
        box.open(URL, AAD)


def test_rotation_keeps_old_values_readable_and_flags_them():
    old_key, new_key = os.urandom(32), os.urandom(32)
    old_sealed = SecretBox.from_key(old_key).seal(URL, AAD)
    rotated = SecretBox.from_key(new_key, previous=(old_key,))
    assert rotated.open(old_sealed, AAD) == URL
    assert rotated.needs_reseal(old_sealed) and rotated.needs_reseal(URL)
    assert not rotated.needs_reseal(rotated.seal(URL, AAD))
    assert rotated.active == key_id(new_key)


def test_key_files(tmp_path):
    key = os.urandom(32)
    for name, content in (("raw.key", key), ("b64.key", base64.b64encode(key) + b"\n"), ("hex.key", key.hex().encode())):
        path = tmp_path / name
        path.write_bytes(content)
        path.chmod(0o600)
        assert read_key_file(str(path)) == key
    loose = tmp_path / "loose.key"
    loose.write_bytes(key)
    loose.chmod(0o644)
    with pytest.raises(KeyConfigError):
        read_key_file(str(loose))
    short = tmp_path / "short.key"
    short.write_bytes(base64.b64encode(os.urandom(16)))
    short.chmod(0o600)
    with pytest.raises(KeyConfigError):
        read_key_file(str(short))
    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "old.key").write_bytes(os.urandom(32))
    (previous / "old.key").chmod(0o600)
    assert len(load_secret_box(str(tmp_path / "raw.key"), str(previous)).keys) == 2


class Base(DeclarativeBase):
    pass


BOX: dict = {"box": None}


class Row(Base):
    __tablename__ = "rows"
    id = Column(Integer, primary_key=True)
    secret = Column(SealedString(lambda: BOX["box"], AAD))


def test_column_type_seals_on_write_and_opens_on_read():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    BOX["box"] = None
    with Session(engine) as session:
        session.add(Row(id=1, secret=URL))  # development: stored as is
        session.commit()
    BOX["box"] = SecretBox.from_key(os.urandom(32))
    with Session(engine) as session:
        session.add(Row(id=2, secret=URL))
        session.commit()
        raw = dict(session.execute(text("select id, secret from rows")).all())
        assert raw[1] == URL  # legacy plaintext, still readable...
        assert raw[2].startswith(PREFIX) and "S3cret" not in raw[2]
        assert [r.secret for r in session.scalars(select(Row).order_by(Row.id))] == [URL, URL]
    BOX["box"] = None
    with Session(engine) as session, pytest.raises(SecretBoxError):
        session.get(Row, 2)  # sealed data never silently returned without a key


# -- API masking round trip ---------------------------------------------------

from argus_common.net import MaskedCredentialsError, redact_url, restore_masked_credentials  # noqa: E402


def test_masked_urls_sent_back_keep_the_stored_credentials():
    stored = "rtsp://admin:S3cret!@192.0.2.10:554/Streaming/Channels/101"
    masked = redact_url(stored)
    assert "S3cret" not in masked and "admin" not in masked
    assert restore_masked_credentials(masked, stored) == stored
    # Path edited, address unchanged: credentials kept.
    edited = masked.replace("101", "102")
    assert restore_masked_credentials(edited, stored) == stored.replace("101", "102")
    # New credentials typed in: taken as given.
    typed = "rtsp://viewer:n3w@192.0.2.10:554/Streaming/Channels/101"
    assert restore_masked_credentials(typed, stored) == typed


def test_masked_credentials_never_follow_a_changed_address():
    stored = "rtsp://admin:S3cret!@192.0.2.10:554/live"
    for moved in ("rtsp://***@198.51.100.7:554/live", "rtsp://***@192.0.2.10:8554/live", "rtsps://***@192.0.2.10:554/live"):
        with pytest.raises(MaskedCredentialsError):
            restore_masked_credentials(moved, stored)


def test_masked_query_secrets_are_restored():
    stored = "rtsp://192.0.2.10/live?user=ops&password=hunter2"
    masked = redact_url(stored)
    assert "hunter2" not in masked
    assert restore_masked_credentials(masked, stored) == stored
    with pytest.raises(MaskedCredentialsError):
        restore_masked_credentials("rtsp://192.0.2.10/live?password=***", "rtsp://192.0.2.10/live")
