"""The one-time parking data migration copies, skips duplicates and scopes cameras."""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_script():
    path = SERVICE_DIR / "scripts" / "migrate_parking_data.py"
    spec = importlib.util.spec_from_file_location("migrate_parking_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE cameras (id TEXT PRIMARY KEY, name TEXT, location TEXT, stream_url TEXT,
                tenant_id TEXT, status TEXT, resolution TEXT, fps INTEGER, role TEXT, gate_roi JSON,
                calibration JSON, created_at DATETIME, updated_at DATETIME, is_active BOOLEAN);
            CREATE TABLE vehicle_profiles (id TEXT PRIMARY KEY, tenant_id TEXT, plate_text TEXT,
                profile_type TEXT, owner_name TEXT, notes TEXT, created_at DATETIME, updated_at DATETIME);
            INSERT INTO cameras (id, name, location, stream_url, tenant_id, role, is_active, fps)
                VALUES ('gate-1', 'Gate', 'N', 'rtsp://203.0.113.1/x', 't1', 'gate_entry', 1, 30),
                       ('perim-1', 'Perimeter', 'S', 'rtsp://203.0.113.2/x', 't1', 'surveillance', 1, 30);
            INSERT INTO vehicle_profiles (id, tenant_id, plate_text, profile_type)
                VALUES ('v1', 't1', 'KA01AB1234', 'resident');
            """
        )


def test_migrates_parking_rows_and_only_parking_cameras(tmp_path, monkeypatch):
    source, target = tmp_path / "surveillance.db", tmp_path / "parking.db"
    _source_db(source)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{target}")
    config = Config(str(SERVICE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_DIR / "alembic"))
    command.upgrade(config, "head")

    script = _load_script()
    first = script.migrate(f"sqlite:///{source}", f"sqlite:///{target}")
    assert first == {"cameras": 1, "vehicle_profiles": 1}
    again = script.migrate(f"sqlite:///{source}", f"sqlite:///{target}", delete_source=True)
    assert again == {"cameras": 0, "vehicle_profiles": 0}

    with sqlite3.connect(target) as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM cameras")] == ["gate-1"]
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM vehicle_profiles").fetchone()[0] == 0
        assert [r[0] for r in conn.execute("SELECT id FROM cameras")] == ["perim-1"]
