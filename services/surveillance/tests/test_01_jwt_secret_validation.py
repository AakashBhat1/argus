"""
TEST 1 - JWT Secret Validation at Startup

RED: auth.py defaults SECRET_KEY to "super-secret-key-change-in-production"
     with no startup enforcement. Tests expect RuntimeError when insecure
     default is used in production mode.

GREEN: Add startup validation that raises RuntimeError when SECRET_KEY
       equals the known default AND DEBUG is False.
"""

from __future__ import annotations

import os
import sys

import pytest


# Save the original auth module reference so we can restore it after each test.
# This prevents downstream tests from seeing a stale/reimported module.
_original_auth_module = sys.modules.get("app.services.auth")


def _purge_auth_module():
    """Remove app.services.auth from sys.modules and clear the cached attribute."""
    for mod in list(sys.modules.keys()):
        if "app.services.auth" in mod:
            del sys.modules[mod]
    # Also clear the cached 'auth' attribute on the parent package
    services_mod = sys.modules.get("app.services")
    if services_mod and hasattr(services_mod, "auth"):
        delattr(services_mod, "auth")


def _restore_auth_module():
    """Put the original auth module back into sys.modules."""
    if _original_auth_module is not None:
        sys.modules["app.services.auth"] = _original_auth_module
        services_mod = sys.modules.get("app.services")
        if services_mod:
            services_mod.auth = _original_auth_module


class TestJwtSecretValidation:
    def teardown_method(self):
        """Restore the original auth module after each test."""
        _restore_auth_module()

    def test_default_secret_raises_in_production(self, monkeypatch):
        """
        RED: When SECRET_KEY is the insecure default and DEBUG=False,
        importing the auth module should raise RuntimeError.
        """
        monkeypatch.setenv("SECRET_KEY", "super-secret-key-change-in-production")
        monkeypatch.setenv("DEBUG", "false")
        _purge_auth_module()

        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            from app.services import auth  # noqa: F401

    def test_custom_secret_does_not_raise(self, monkeypatch):
        """A custom, sufficiently long secret must not raise."""
        monkeypatch.setenv("SECRET_KEY", "a-very-secure-random-secret-value-123!")
        monkeypatch.setenv("DEBUG", "false")
        _purge_auth_module()

        from app.services import auth  # noqa: F401

    def test_short_secret_raises(self, monkeypatch):
        """
        RED: Secrets shorter than 32 characters are cryptographically
        weak. The current code accepts any string.
        """
        monkeypatch.setenv("SECRET_KEY", "tooshort")
        monkeypatch.setenv("DEBUG", "false")
        _purge_auth_module()

        with pytest.raises(RuntimeError, match="SECRET_KEY"):
            from app.services import auth  # noqa: F401

    def test_default_secret_allowed_in_debug_mode(self, monkeypatch):
        """The default secret is tolerable in DEBUG=True (dev laptops)."""
        monkeypatch.setenv("SECRET_KEY", "super-secret-key-change-in-production")
        monkeypatch.setenv("DEBUG", "true")
        _purge_auth_module()

        from app.services import auth  # noqa: F401
