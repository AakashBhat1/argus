"""Copy parking data from the surveillance database into the parking database.

Run once when splitting an existing single-database deployment:

    python scripts/migrate_parking_data.py \
        --source postgresql://surveillance:***@db:5432/surveillance \
        --target postgresql://parking:***@parking-db:5432/parking

The target schema must exist (``alembic upgrade head`` in services/parking).
Rows are copied by primary key and existing ids are skipped, so re-running
is safe. Cameras with a parking role (gate_entry, gate_exit, parking) are
copied too. Nothing is deleted from the source unless ``--delete-source``
is given; afterwards run the surveillance migration with
ARGUS_PARKING_DATA_MIGRATED=1 to drop the old parking tables.
"""

from __future__ import annotations

import argparse
import sys

import sqlalchemy as sa

PARKING_ROLES = ("gate_entry", "gate_exit", "parking")
# Parents before children (foreign keys).
TABLES = (
    "vehicle_profiles",
    "parking_spaces",
    "detected_plates",
    "parking_sessions",
    "parking_activity_log",
)


def _sync_url(url: str) -> str:
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")


def _copy(source: sa.Connection, target: sa.Connection, table: str, where=None) -> int:
    src_table = sa.Table(table, sa.MetaData(), autoload_with=source)
    dst_table = sa.Table(table, sa.MetaData(), autoload_with=target)
    columns = [c.name for c in dst_table.columns if c.name in src_table.columns]
    existing = {row[0] for row in target.execute(sa.select(dst_table.c.id))}
    query = sa.select(*[src_table.c[name] for name in columns])
    if where is not None:
        query = query.where(where(src_table))
    rows = [dict(row._mapping) for row in source.execute(query) if row._mapping["id"] not in existing]
    if rows:
        target.execute(dst_table.insert(), rows)
    return len(rows)


def migrate(source_url: str, target_url: str, delete_source: bool = False) -> dict[str, int]:
    source_engine = sa.create_engine(_sync_url(source_url))
    target_engine = sa.create_engine(_sync_url(target_url))
    copied: dict[str, int] = {}
    with source_engine.begin() as source, target_engine.begin() as target:
        source_tables = set(sa.inspect(source).get_table_names())
        if "cameras" in source_tables:
            copied["cameras"] = _copy(
                source, target, "cameras", where=lambda t: t.c.role.in_(PARKING_ROLES)
            )
        for table in TABLES:
            if table in source_tables:
                copied[table] = _copy(source, target, table)
        if delete_source:
            for table in reversed(TABLES):
                if table in source_tables:
                    source.execute(sa.text(f'DELETE FROM "{table}"'))
            if "cameras" in source_tables:
                cameras = sa.Table("cameras", sa.MetaData(), autoload_with=source)
                source.execute(cameras.delete().where(cameras.c.role.in_(PARKING_ROLES)))
    return copied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="surveillance database URL")
    parser.add_argument("--target", required=True, help="parking database URL")
    parser.add_argument("--delete-source", action="store_true", help="remove copied parking rows from the source")
    args = parser.parse_args(argv)
    for table, count in migrate(args.source, args.target, args.delete_source).items():
        print(f"{table}: {count} row(s) copied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
