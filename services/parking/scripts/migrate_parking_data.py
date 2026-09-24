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

Camera stream URLs are sealed at rest with each service's own key. If the
surveillance database already holds sealed URLs, pass its key with
``--source-camera-key``; pass parking's key with ``--target-camera-key`` to
seal them for parking right away (otherwise parking seals them at startup).
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Optional

import sqlalchemy as sa

from argus_common.secretbox import SecretBox, load_secret_box

PARKING_ROLES = ("gate_entry", "gate_exit", "parking")
SOURCE_URL_AAD = "argus-surveillance:cameras.stream_url"
TARGET_URL_AAD = "argus-parking:cameras.stream_url"
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


def _copy(
    source: sa.Connection,
    target: sa.Connection,
    table: str,
    where=None,
    transform: Optional[Callable[[dict], dict]] = None,
) -> int:
    src_table = sa.Table(table, sa.MetaData(), autoload_with=source)
    dst_table = sa.Table(table, sa.MetaData(), autoload_with=target)
    columns = [c.name for c in dst_table.columns if c.name in src_table.columns]
    existing = {row[0] for row in target.execute(sa.select(dst_table.c.id))}
    query = sa.select(*[src_table.c[name] for name in columns])
    if where is not None:
        query = query.where(where(src_table))
    rows = [dict(row._mapping) for row in source.execute(query) if row._mapping["id"] not in existing]
    if transform is not None:
        rows = [transform(row) for row in rows]
    if rows:
        target.execute(dst_table.insert(), rows)
    return len(rows)


def _reseal(source_box: Optional[SecretBox], target_box: Optional[SecretBox]) -> Callable[[dict], dict]:
    def transform(row: dict) -> dict:
        url = row.get("stream_url")
        if SecretBox.is_sealed(url):
            if source_box is None:
                raise SystemExit(
                    f"camera {row['id']}: stream URL is sealed; pass --source-camera-key "
                    "(surveillance's CAMERA_SECRETS_KEY_FILE)"
                )
            url = source_box.open(url, SOURCE_URL_AAD)
        if url is not None and target_box is not None:
            url = target_box.seal(url, TARGET_URL_AAD)
        return {**row, "stream_url": url}

    return transform


def migrate(
    source_url: str,
    target_url: str,
    delete_source: bool = False,
    source_box: Optional[SecretBox] = None,
    target_box: Optional[SecretBox] = None,
) -> dict[str, int]:
    source_engine = sa.create_engine(_sync_url(source_url))
    target_engine = sa.create_engine(_sync_url(target_url))
    copied: dict[str, int] = {}
    with source_engine.begin() as source, target_engine.begin() as target:
        source_tables = set(sa.inspect(source).get_table_names())
        if "cameras" in source_tables:
            copied["cameras"] = _copy(
                source,
                target,
                "cameras",
                where=lambda t: t.c.role.in_(PARKING_ROLES),
                transform=_reseal(source_box, target_box),
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
    parser.add_argument("--source-camera-key", help="surveillance's camera-secrets key file")
    parser.add_argument("--target-camera-key", help="parking's camera-secrets key file")
    args = parser.parse_args(argv)
    source_box = load_secret_box(args.source_camera_key) if args.source_camera_key else None
    target_box = load_secret_box(args.target_camera_key) if args.target_camera_key else None
    for table, count in migrate(args.source, args.target, args.delete_source, source_box, target_box).items():
        print(f"{table}: {count} row(s) copied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
