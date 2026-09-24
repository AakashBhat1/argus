#!/usr/bin/env python
"""
Copy classifier data from local SQLite to PostgreSQL.

Usage (from backend/yolo_classifier):
    python scripts/migrate_sqlite_to_postgres.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterable

import psycopg2
from psycopg2.extras import Json, execute_values

# Make "app" imports resolvable when run as a script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DB_PATH, get_settings
from app.database import init_db


TABLES = [
    {
        "name": "users",
        "columns": [
            "id",
            "username",
            "hashed_password",
            "role",
            "tenant_id",
            "created_at",
            "is_active",
        ],
        "bool_columns": {"is_active"},
        "json_columns": set(),
    },
    {
        "name": "cameras",
        "columns": [
            "id",
            "name",
            "location",
            "stream_url",
            "tenant_id",
            "status",
            "resolution",
            "fps",
            "created_at",
            "updated_at",
            "is_active",
        ],
        "bool_columns": {"is_active"},
        "json_columns": set(),
    },
    {
        "name": "detections",
        "columns": [
            "id",
            "camera_id",
            "tenant_id",
            "object_id",
            "class_label",
            "confidence",
            "bbox_x",
            "bbox_y",
            "bbox_w",
            "bbox_h",
            "timestamp",
            "frame_number",
            "metadata",
        ],
        "bool_columns": set(),
        "json_columns": {"metadata"},
    },
    {
        "name": "alerts",
        "columns": [
            "id",
            "camera_id",
            "tenant_id",
            "type",
            "severity",
            "status",
            "trigger_condition",
            "description",
            "timestamp",
            "resolved_at",
            "metadata",
        ],
        "bool_columns": set(),
        "json_columns": {"metadata"},
    },
    {
        "name": "roi_events",
        "columns": [
            "id",
            "tenant_id",
            "camera_id",
            "timestamp",
            "event_type",
            "zone",
            "metadata",
            "frame_number",
            "has_intrusion",
            "has_movement",
            "classes",
            "raw_event",
        ],
        "bool_columns": {"has_intrusion", "has_movement"},
        "json_columns": {"metadata", "classes", "raw_event"},
    },
    {
        "name": "analytics_snapshots",
        "columns": [
            "id",
            "camera_id",
            "tenant_id",
            "period_start",
            "period_end",
            "total_detections",
            "unique_objects",
            "class_counts",
            "avg_confidence",
            "peak_count",
            "created_at",
        ],
        "bool_columns": set(),
        "json_columns": {"class_counts"},
    },
]


def _normalize_postgres_dsn(raw_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""
    if value.startswith("postgresql+asyncpg://"):
        return value.replace("postgresql+asyncpg://", "postgresql://", 1)
    if value.startswith("postgresql+psycopg2://"):
        return value.replace("postgresql+psycopg2://", "postgresql://", 1)
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql://", 1)
    if value.startswith("postgresql://"):
        return value
    return ""


def _sqlite_has_table(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _sqlite_table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _sqlite_has_table(conn, table_name):
        return set()
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}


def _count_rows_sqlite(conn: sqlite3.Connection, table_name: str) -> int:
    if not _sqlite_has_table(conn, table_name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])


def _count_rows_pg(conn, table_name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        return int(cur.fetchone()[0])


def _adapt_value(column: str, value, bool_columns: set[str], json_columns: set[str]):
    if value is None:
        if column in json_columns:
            return Json({})
        return None

    if column in bool_columns:
        return bool(value)

    if column in json_columns:
        if isinstance(value, (dict, list)):
            return Json(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return Json({})
            try:
                return Json(json.loads(text))
            except json.JSONDecodeError:
                return Json({})
        return Json({})

    return value


def _migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn,
    table_name: str,
    columns: Iterable[str],
    bool_columns: set[str],
    json_columns: set[str],
    batch_size: int,
) -> tuple[int, int, int]:
    if not _sqlite_has_table(sqlite_conn, table_name):
        print(f"[skip] {table_name}: table not found in SQLite")
        return (0, 0, 0)

    columns = list(columns)
    sqlite_cols = _sqlite_table_columns(sqlite_conn, table_name)
    insert_columns = [col for col in columns if col in sqlite_cols]
    if not insert_columns:
        print(f"[skip] {table_name}: no overlapping columns between SQLite and PostgreSQL schema")
        return (0, 0, 0)

    missing_columns = [col for col in columns if col not in sqlite_cols]
    if missing_columns:
        print(f"[info] {table_name}: missing SQLite columns will use PostgreSQL defaults: {missing_columns}")

    column_sql = ", ".join(insert_columns)
    before_pg = _count_rows_pg(pg_conn, table_name)
    source_count = _count_rows_sqlite(sqlite_conn, table_name)

    src_cur = sqlite_conn.cursor()
    src_cur.execute(f"SELECT {column_sql} FROM {table_name}")

    insert_sql = f"INSERT INTO {table_name} ({column_sql}) VALUES %s ON CONFLICT DO NOTHING"
    processed = 0

    with pg_conn.cursor() as dst_cur:
        while True:
            rows = src_cur.fetchmany(batch_size)
            if not rows:
                break

            payload = []
            for row in rows:
                adapted = [
                    _adapt_value(col, val, bool_columns, json_columns)
                    for col, val in zip(insert_columns, row)
                ]
                payload.append(tuple(adapted))

            execute_values(dst_cur, insert_sql, payload, page_size=batch_size)
            processed += len(payload)

    pg_conn.commit()
    after_pg = _count_rows_pg(pg_conn, table_name)
    inserted = max(0, after_pg - before_pg)
    print(
        f"[ok] {table_name}: sqlite={source_count}, processed={processed}, "
        f"inserted={inserted}, pg_total={after_pg}"
    )
    return (source_count, processed, inserted)


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL.")
    parser.add_argument(
        "--sqlite-path",
        default=DB_PATH,
        help=f"Path to SQLite DB (default: {DB_PATH})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows per insert batch (default: 1000)",
    )
    args = parser.parse_args()

    settings = get_settings()
    pg_dsn = _normalize_postgres_dsn(str(settings.DATABASE_URL))
    if not pg_dsn:
        print("ERROR: DATABASE_URL is missing or not a PostgreSQL URL.")
        return 1

    sqlite_path = str(Path(args.sqlite_path).resolve())
    if not Path(sqlite_path).exists():
        print(f"ERROR: SQLite database not found: {sqlite_path}")
        return 1

    # Ensure target tables/indexes exist on PostgreSQL before inserting.
    asyncio.run(init_db())

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    try:
        pg_conn = psycopg2.connect(pg_dsn)
    except Exception as exc:
        print(f"ERROR: Failed to connect PostgreSQL: {exc}")
        sqlite_conn.close()
        return 1

    totals = {"sqlite": 0, "processed": 0, "inserted": 0}
    try:
        print(f"SQLite source: {sqlite_path}")
        print("Starting migration...")
        for spec in TABLES:
            src, processed, inserted = _migrate_table(
                sqlite_conn=sqlite_conn,
                pg_conn=pg_conn,
                table_name=spec["name"],
                columns=spec["columns"],
                bool_columns=spec["bool_columns"],
                json_columns=spec["json_columns"],
                batch_size=args.batch_size,
            )
            totals["sqlite"] += src
            totals["processed"] += processed
            totals["inserted"] += inserted
    finally:
        sqlite_conn.close()
        pg_conn.close()

    print(
        "Done. "
        f"sqlite_rows={totals['sqlite']} processed={totals['processed']} inserted={totals['inserted']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
