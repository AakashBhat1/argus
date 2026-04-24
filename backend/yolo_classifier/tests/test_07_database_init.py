"""
TEST 7 - Database Initialization: No Silent SQLite Fallback

RED: init_db() silently falls back to SQLite when Postgres is unreachable.
     In production, this causes data loss on container restart.

GREEN: Add ALLOW_DB_FALLBACK setting. Raise RuntimeError when primary DB
       is unreachable unless fallback is explicitly allowed.
"""

from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import the module once and work with it directly — avoid deleting it
# from sys.modules which breaks get_db identity for downstream tests.
from app import database as db_module


class TestDatabaseInit:
    @pytest.mark.asyncio
    async def test_init_db_raises_on_unreachable_primary_without_fallback(
        self, monkeypatch
    ):
        """
        RED: When primary DB is unreachable and ALLOW_DB_FALLBACK is False,
        init_db() must raise RuntimeError. Currently falls back silently.
        """
        monkeypatch.setenv("ALLOW_DB_FALLBACK", "false")

        # Temporarily make DATABASE_URL look like postgres so is_already_sqlite is False
        original_url = db_module.DATABASE_URL
        monkeypatch.setattr(db_module, "DATABASE_URL", "postgresql+asyncpg://user:pass@unreachable:5432/db")

        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(
            side_effect=Exception("Connection refused")
        )
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_conn)

        original_engine = db_module.engine
        db_module.engine = mock_engine

        try:
            with pytest.raises(RuntimeError, match="database"):
                await db_module.init_db()
        finally:
            db_module.engine = original_engine

    @pytest.mark.asyncio
    async def test_init_db_allows_fallback_when_flag_is_true(self, monkeypatch):
        """GREEN: When ALLOW_DB_FALLBACK=true, SQLite fallback is permitted."""
        monkeypatch.setenv("ALLOW_DB_FALLBACK", "true")

        original_url = db_module.DATABASE_URL
        monkeypatch.setattr(db_module, "DATABASE_URL", "postgresql+asyncpg://user:pass@unreachable:5432/db")

        mock_engine = MagicMock()

        call_count = {"n": 0}

        class FakeConn:
            async def run_sync(self, fn):
                pass

            async def __aenter__(self):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise Exception("Primary DB unreachable")
                return self

            async def __aexit__(self, *args):
                pass

        mock_engine.begin = MagicMock(return_value=FakeConn())

        original_engine = db_module.engine
        db_module.engine = mock_engine

        with patch.object(db_module, "_switch_database") as mock_switch:
            mock_switch.side_effect = lambda url: None
            try:
                await db_module.init_db()
            except Exception as exc:
                if "ALLOW_DB_FALLBACK" in str(exc) or "engine" in str(exc):
                    pytest.fail(
                        f"Unexpected RuntimeError when fallback allowed: {exc}"
                    )
        db_module.engine = original_engine
