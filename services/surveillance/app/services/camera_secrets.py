"""Camera stream credentials at rest (argus_common.secretbox).

Stream URLs are sealed in the database with this service's camera-secrets
key (deploy/pki.sh creates it; back it up, it cannot be regenerated).
Production refuses to start without it; DEBUG may store plaintext.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from sqlalchemy import select, type_coerce, update
from sqlalchemy.types import Text

from app.config import get_settings
from argus_common.keys import KeyConfigError
from argus_common.secretbox import SecretBox, load_secret_box

logger = logging.getLogger(__name__)

STREAM_URL_AAD = "argus-surveillance:cameras.stream_url"

_lock = threading.Lock()
_box: Optional[SecretBox] = None
_warned = False


def camera_secret_box() -> Optional[SecretBox]:
    global _box, _warned
    if _box is not None:
        return _box
    settings = get_settings()
    if not settings.CAMERA_SECRETS_KEY_FILE:
        if settings.DEBUG:
            if not _warned:
                logger.warning("DEBUG: CAMERA_SECRETS_KEY_FILE unset; camera credentials are stored unencrypted")
                _warned = True
            return None
        raise RuntimeError("CAMERA_SECRETS_KEY_FILE is required (32-byte key, see deploy/pki.sh)")
    with _lock:
        if _box is None:
            try:
                _box = load_secret_box(settings.CAMERA_SECRETS_KEY_FILE, settings.CAMERA_SECRETS_PREVIOUS_KEYS_DIR)
            except KeyConfigError as exc:
                raise RuntimeError(f"Camera secrets key unavailable: {exc}") from exc
    return _box


def reset_camera_secret_box() -> None:
    global _box, _warned
    with _lock:
        _box = None
        _warned = False


async def seal_stored_stream_urls(session_factory) -> int:
    """Seal plaintext URLs (and re-seal ones under retired keys). Idempotent."""
    box = camera_secret_box()
    if box is None:
        return 0
    from app.models import Camera

    table = Camera.__table__
    async with session_factory() as session:
        rows = (await session.execute(select(table.c.id, type_coerce(table.c.stream_url, Text)))).all()
        stale = [(camera_id, raw) for camera_id, raw in rows if raw is not None and box.needs_reseal(raw)]
        for camera_id, raw in stale:
            plaintext = box.open(raw, STREAM_URL_AAD) if box.is_sealed(raw) else raw
            await session.execute(
                update(table).where(table.c.id == camera_id).values(stream_url=box.seal(plaintext, STREAM_URL_AAD))
            )
        await session.commit()
    if stale:
        logger.info("Sealed %d camera stream URL(s)", len(stale))
    return len(stale)
