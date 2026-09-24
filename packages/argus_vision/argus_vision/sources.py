"""Camera source resolution and capture (shared by every pipeline).

``video://<name>`` resolves into configured local folders, digit strings are
local capture devices, and network URLs are re-validated against the SSRF
policy immediately before connecting.
"""

from __future__ import annotations

import base64
import logging
import platform
from pathlib import Path
from typing import Optional, Union
from urllib.parse import quote

import cv2
import numpy as np

from argus_common.net import (
    StreamTargetError,
    is_network_source,
    policy_from_settings,
    recheck_stream_target,
    redact_url,
    validate_stream_target,
)
from argus_vision.settings import get_settings

logger = logging.getLogger(__name__)

Source = Union[int, str]


def video_source_roots() -> list[Path]:
    """Directories that ``video://<name>`` sources may resolve into.

    ``VIDEO_SOURCE_DIRS`` overrides the defaults (``<service>/video`` and the
    repository-root ``video/``). Video files are local operator data and are
    never committed.
    """
    settings = get_settings()
    configured = [Path(p).expanduser() for p in settings.VIDEO_SOURCE_DIRS if str(p).strip()]
    if configured:
        return configured
    service_dir = Path(settings.SERVICE_DIR).resolve()
    roots = [service_dir / "video"]
    if len(service_dir.parents) >= 2:
        roots.append(service_dir.parents[1] / "video")
    return roots


def resolve_stream_source(stream_url: str) -> Source:
    """Map digit strings to device indexes and ``video://`` URIs to files."""
    value = stream_url.strip()
    if value.isdigit():
        return int(value)
    if value.startswith("video://"):
        filename = value[len("video://"):]
        relative_path = Path(filename)
        if not filename or relative_path.is_absolute() or ".." in relative_path.parts:
            logger.warning("Rejected unsafe video:// source: %s", value)
            return value
        for root in video_source_roots():
            candidate = (root / relative_path).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                continue
            if candidate.exists():
                return str(candidate)
        logger.warning("video:// source not found in configured roots: %s", value)
        return value
    return value


def open_capture(stream_url: Union[str, int]) -> cv2.VideoCapture:
    """Open a capture for URLs, ``video://`` files or local device indexes.

    Network sources are re-checked against the SSRF policy right before the
    connection, so a hostname re-pointed at an internal address after the
    camera was saved is still refused.
    """
    source = stream_url if isinstance(stream_url, int) else resolve_stream_source(stream_url)
    if isinstance(source, str) and is_network_source(source):
        try:
            recheck_stream_target(source, policy_from_settings(get_settings()))
        except StreamTargetError as exc:
            logger.warning("Refusing stream source %s: %s", redact_url(source), exc)
            return cv2.VideoCapture()
    if isinstance(source, int) and platform.system().lower() == "windows":
        return cv2.VideoCapture(source, cv2.CAP_DSHOW)
    cap = cv2.VideoCapture(source)
    if isinstance(source, str) and (source.startswith("http") or source.startswith("rtsp")):
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 10000)
    return cap


def encode_frame_to_base64(frame: np.ndarray, quality: int = 70) -> str:
    """JPEG-encode a frame for WebSocket transport (local file sources)."""
    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not success:
        return ""
    return base64.b64encode(buffer.tobytes()).decode("ascii")


def mediamtx_can_pull(stream_url: str) -> bool:
    """Whether MediaMTX can proxy the source directly (RTSP only)."""
    return stream_url.partition(":")[0].strip().lower() in {"rtsp", "rtsps"}


def mediamtx_read_url(camera_id: str, settings: Optional[object] = None) -> str:
    """URL the analytics pipeline reads when MediaMTX already ingests a camera.

    Reading the MediaMTX path instead of the camera keeps one RTSP session per
    camera; low-power IP cameras often cap concurrent sessions.
    """
    settings = settings or get_settings()
    base = settings.MEDIAMTX_RTSP_READ_BASE_URL.rstrip("/")
    scheme, sep, rest = base.partition("://")
    user = settings.MEDIAMTX_READ_USERNAME
    password = settings.MEDIAMTX_READ_PASSWORD
    if sep and user:
        creds = quote(user, safe="")
        if password:
            creds += ":" + quote(password, safe="")
        base = f"{scheme}://{creds}@{rest}"
    return f"{base}/{quote(str(camera_id), safe='')}"


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv", ".wmv", ".m4v"}


class SourceError(ValueError):
    """A camera source is not acceptable; the message is safe to return."""


def validate_camera_source(stream_url: str) -> None:
    """Validate operator-supplied camera sources (SSRF and file access).

    Accepts a local capture index (if enabled), ``video://<file>`` inside the
    configured video folders, or a network URL permitted by the SSRF policy.
    Bare filesystem paths and ``file://`` URLs are rejected.
    """
    settings = get_settings()
    value = (stream_url or "").strip()
    if not value:
        raise SourceError("stream_url must not be empty.")
    if value.lower() == "string":
        raise SourceError(
            "Invalid stream_url placeholder 'string'. Use webcam index (e.g. '0'), RTSP URL, or file path."
        )
    if value.isdigit():
        if not settings.ALLOW_LOCAL_CAPTURE_DEVICES:
            raise SourceError("Local capture devices are disabled (ALLOW_LOCAL_CAPTURE_DEVICES=false).")
        return
    if value.startswith("video://"):
        filename = value[len("video://"):]
        if not filename or "/" in filename or "\\" in filename:
            raise SourceError("Invalid video filename. Use format: video://filename.mp4")
        resolved = resolve_stream_source(value)
        video_path = Path(resolved) if isinstance(resolved, str) else None
        if resolved == value or video_path is None or not video_path.is_file():
            raise SourceError(f"Video file '{filename}' not found in video folder.")
        if video_path.suffix.lower() not in VIDEO_EXTENSIONS:
            raise SourceError(
                f"Unsupported video format '{video_path.suffix}'. "
                f"Allowed: {', '.join(sorted(VIDEO_EXTENSIONS))}"
            )
        return
    if "://" not in value:
        raise SourceError(
            "stream_url must be an rtsp(s):// URL, a video:// file, or a webcam index."
        )
    try:
        validate_stream_target(value, policy_from_settings(settings))
    except StreamTargetError as exc:
        raise SourceError(str(exc)) from exc
