"""SSRF policy for camera sources: resolution, allowlists and redaction."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.security.net import (
    StreamTargetError,
    StreamTargetPolicy,
    policy_from_settings,
    recheck_stream_target,
    redact_url,
    validate_stream_target,
)


def _resolver(mapping: dict[str, list[str]]):
    def resolve(host: str, port: int):
        if host not in mapping:
            raise OSError("NXDOMAIN")
        return mapping[host]

    return resolve


STRICT = StreamTargetPolicy.build()
LAN = StreamTargetPolicy.build(network_allowlist=["192.168.10.0/24"])


def test_hostname_resolving_to_metadata_is_rejected():
    resolver = _resolver({"evil.example": ["169.254.169.254"]})
    with pytest.raises(StreamTargetError, match="metadata"):
        validate_stream_target("rtsp://evil.example/cam", LAN, resolver)


def test_metadata_cannot_be_allowlisted():
    policy = StreamTargetPolicy.build(network_allowlist=["169.254.0.0/16"])
    with pytest.raises(StreamTargetError, match="metadata"):
        validate_stream_target("rtsp://169.254.169.254/x", policy)


def test_hostname_resolving_to_private_address_needs_allowlist():
    resolver = _resolver({"cam.lan": ["192.168.10.20"]})
    with pytest.raises(StreamTargetError, match="CAMERA_NETWORK_ALLOWLIST"):
        validate_stream_target("rtsp://cam.lan/stream", STRICT, resolver)
    target = validate_stream_target("rtsp://cam.lan/stream", LAN, resolver)
    assert target.addresses == ("192.168.10.20",)


def test_private_address_outside_allowlist_is_rejected():
    with pytest.raises(StreamTargetError):
        validate_stream_target("rtsp://192.168.99.5/stream", LAN)


def test_any_bad_resolved_address_rejects_the_host():
    resolver = _resolver({"mixed.example": ["203.0.113.9", "127.0.0.1"]})
    with pytest.raises(StreamTargetError, match="loopback"):
        validate_stream_target("rtsp://mixed.example/s", STRICT, resolver)


def test_ipv4_mapped_ipv6_loopback_is_rejected():
    with pytest.raises(StreamTargetError):
        validate_stream_target("rtsp://[::ffff:127.0.0.1]:554/s", STRICT)


def test_unresolvable_host_is_rejected():
    with pytest.raises(StreamTargetError, match="could not be resolved"):
        validate_stream_target("rtsp://nowhere.invalid/s", STRICT, _resolver({}))


def test_http_requires_explicit_opt_in():
    with pytest.raises(StreamTargetError, match="scheme"):
        validate_stream_target("http://203.0.113.5/video", STRICT)
    policy = StreamTargetPolicy.build(allow_http=True)
    validate_stream_target("http://203.0.113.5/video", policy)


def test_trusted_media_host_must_match_port():
    policy = StreamTargetPolicy.build(host_allowlist=["mediamtx:8554"])
    resolver = _resolver({"mediamtx": ["172.18.0.4"]})
    assert validate_stream_target("rtsp://mediamtx:8554/cam", policy, resolver).trusted_host
    with pytest.raises(StreamTargetError):
        validate_stream_target("rtsp://mediamtx:9997/v3/config", policy, resolver)


def test_recheck_catches_dns_rebinding_after_save():
    records = {"cam.example": ["203.0.113.7"]}
    resolver = _resolver(records)
    validate_stream_target("rtsp://cam.example/s", STRICT, resolver)
    records["cam.example"] = ["10.0.0.5"]
    with pytest.raises(StreamTargetError):
        recheck_stream_target("rtsp://cam.example/s", STRICT, resolver)


def test_legacy_private_flag_maps_to_rfc1918_and_http_only():
    class LegacySettings:
        CAMERA_NETWORK_ALLOWLIST: list[str] = []
        STREAM_HOST_ALLOWLIST: list[str] = []
        ALLOW_HTTP_STREAMS = False
        ALLOW_PRIVATE_STREAM_URLS = True

    policy = policy_from_settings(LegacySettings())
    validate_stream_target("http://192.168.1.20:4747/video", policy)
    with pytest.raises(StreamTargetError):
        validate_stream_target("rtsp://127.0.0.1:8554/s", policy)


def test_invalid_cidr_is_a_configuration_error():
    with pytest.raises(ValueError, match="CIDR"):
        StreamTargetPolicy.build(network_allowlist=["not-a-cidr"])


@pytest.mark.parametrize(
    "url, expected",
    [
        ("rtsp://admin:hunter2@192.168.1.5:554/s", "rtsp://***@192.168.1.5:554/s"),
        (
            "rtsp://10.0.0.2:554/user=admin&password=abc&channel=1",
            "rtsp://10.0.0.2:554/user=admin&password=***&channel=1",
        ),
        ("video://clip.mp4", "video://clip.mp4"),
        ("0", "0"),
    ],
)
def test_redact_url(url, expected):
    assert redact_url(url) == expected


def test_router_rejects_bare_file_paths():
    from app.routers.cameras import _validate_stream_url

    for value in ("/etc/passwd", "C:\\videos\\clip.mp4", "file:///etc/passwd"):
        with pytest.raises(HTTPException) as exc_info:
            _validate_stream_url(value)
        assert exc_info.value.status_code == 422
