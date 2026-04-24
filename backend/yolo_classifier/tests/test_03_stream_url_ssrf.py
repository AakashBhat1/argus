"""
TEST 3 - Stream URL SSRF Validation

RED: _validate_stream_url() only rejects the literal placeholder "string".
     It accepts any URL including AWS metadata, file://, and private IPs.

GREEN: Expand validation to block dangerous schemes and private IP ranges.
"""

from __future__ import annotations

import sys
import os

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _get_validator():
    from app.routers.cameras import _validate_stream_url

    return _validate_stream_url


class TestStreamUrlSsrfValidation:
    def test_rejects_aws_metadata_service(self):
        """RED: http://169.254.169.254/ must be rejected."""
        validate = _get_validator()
        with pytest.raises(HTTPException) as exc_info:
            validate("http://169.254.169.254/latest/meta-data/")
        assert exc_info.value.status_code == 422

    def test_rejects_file_uri(self):
        """RED: file:///etc/passwd must be rejected."""
        validate = _get_validator()
        with pytest.raises(HTTPException) as exc_info:
            validate("file:///etc/passwd")
        assert exc_info.value.status_code == 422

    def test_rejects_http_url(self):
        """RED: Plain HTTP URLs are not valid RTSP sources."""
        validate = _get_validator()
        with pytest.raises(HTTPException) as exc_info:
            validate("http://internal-service/api/data")
        assert exc_info.value.status_code == 422

    def test_rejects_localhost_rtsp(self):
        """RED: rtsp://127.0.0.1/ targets localhost."""
        validate = _get_validator()
        with pytest.raises(HTTPException) as exc_info:
            validate("rtsp://127.0.0.1:8554/stream")
        assert exc_info.value.status_code == 422

    def test_rejects_placeholder_string(self):
        """Existing behaviour: 'string' placeholder is rejected."""
        validate = _get_validator()
        with pytest.raises(HTTPException) as exc_info:
            validate("string")
        assert exc_info.value.status_code == 422

    def test_accepts_valid_rtsp_external(self):
        """GREEN: A real external RTSP URL must be accepted."""
        validate = _get_validator()
        validate("rtsp://203.0.113.5:554/stream1")

    def test_accepts_webcam_index(self):
        """GREEN: Local webcam index '0' must still be accepted."""
        validate = _get_validator()
        validate("0")

    def test_rejects_empty_string(self):
        """RED: Empty string is not a valid stream source."""
        validate = _get_validator()
        with pytest.raises(HTTPException) as exc_info:
            validate("")
        assert exc_info.value.status_code == 422

    def test_rejects_private_ip_range_172(self):
        """RED: RFC-1918 172.16.0.0/12 block must be rejected."""
        validate = _get_validator()
        with pytest.raises(HTTPException) as exc_info:
            validate("rtsp://172.20.0.5/cam")
        assert exc_info.value.status_code == 422
