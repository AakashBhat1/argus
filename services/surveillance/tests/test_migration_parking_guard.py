"""The parking-table drop migration never silently destroys data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

SERVICE_DIR = Path(__file__).resolve().parents[1]


def _config(db_path: Path, monkeypatch) -> Config:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    config = Config(str(SERVICE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_DIR / "alembic"))
    return config


def test_drop_refuses_while_parking_tables_hold_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "surveillance.db"
    config = _config(db_path, monkeypatch)
    command.upgrade(config, "20260924_0005")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO vehicle_profiles (id, tenant_id, plate_text) VALUES ('v1', 't1', 'KA01AB1234')"
        )

    monkeypatch.delenv("ARGUS_PARKING_DATA_MIGRATED", raising=False)
    with pytest.raises(RuntimeError, match="migrate_parking_data"):
        command.upgrade(config, "head")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM vehicle_profiles").fetchone()[0] == 1

    monkeypatch.setenv("ARGUS_PARKING_DATA_MIGRATED", "1")
    command.upgrade(config, "head")
    with sqlite3.connect(db_path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "vehicle_profiles" not in names and "outbox_events" in names
