"""Parking tables + Camera.role / gate_roi.

Revision ID: 20260713_0002
Revises: 20260713_0001
Create Date: 2026-07-13

Adds tenant-scoped parking models and gate camera columns.

On production (tables already present via create_all / stamp of baseline),
this only applies the delta. On an empty scratch DB it creates parking
tables; camera column adds are skipped if ``cameras`` is absent (run
``init_db`` / ``create_all`` + ``alembic stamp 20260713_0001`` first in
that case, or create cameras via the app boot path).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "20260713_0002"
down_revision: Union[str, None] = "20260713_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def _column_names(table: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    tables = _table_names()

    if "cameras" in tables:
        cols = _column_names("cameras")
        if "role" not in cols:
            op.add_column(
                "cameras",
                sa.Column(
                    "role",
                    sa.String(length=20),
                    server_default="surveillance",
                    nullable=True,
                ),
            )
        if "gate_roi" not in cols:
            op.add_column(
                "cameras",
                sa.Column("gate_roi", sa.JSON(), nullable=True),
            )

    if "vehicle_profiles" not in tables:
        op.create_table(
            "vehicle_profiles",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("plate_text", sa.String(length=20), nullable=False),
            sa.Column("profile_type", sa.String(length=20), nullable=True),
            sa.Column("owner_name", sa.String(length=255), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_vehicle_profiles_tenant_id", "vehicle_profiles", ["tenant_id"])
        op.create_index(
            "uq_vehicle_profiles_tenant_plate",
            "vehicle_profiles",
            ["tenant_id", "plate_text"],
            unique=True,
        )

    if "parking_spaces" not in tables:
        op.create_table(
            "parking_spaces",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("space_id", sa.String(length=50), nullable=False),
            sa.Column("zone", sa.String(length=10), nullable=True),
            sa.Column("floor", sa.String(length=10), nullable=True),
            sa.Column("is_occupied", sa.Boolean(), nullable=True),
            sa.Column(
                "vehicle_id",
                sa.String(length=36),
                sa.ForeignKey("vehicle_profiles.id"),
                nullable=True,
            ),
            sa.Column("entry_time", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_parking_spaces_tenant_id", "parking_spaces", ["tenant_id"])
        op.create_index(
            "uq_parking_spaces_tenant_space",
            "parking_spaces",
            ["tenant_id", "space_id"],
            unique=True,
        )
        op.create_index(
            "ix_parking_spaces_tenant_occupied",
            "parking_spaces",
            ["tenant_id", "is_occupied"],
        )

    if "detected_plates" not in tables:
        # FK to cameras only if cameras table exists
        camera_fk = (
            sa.ForeignKey("cameras.id") if "cameras" in tables else None
        )
        camera_col = sa.Column(
            "camera_id",
            sa.String(length=36),
            camera_fk,
            nullable=True,
        )
        op.create_table(
            "detected_plates",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("plate_text", sa.String(length=20), nullable=False),
            sa.Column(
                "vehicle_id",
                sa.String(length=36),
                sa.ForeignKey("vehicle_profiles.id"),
                nullable=True,
            ),
            camera_col,
            sa.Column("track_id", sa.String(length=64), nullable=True),
            sa.Column("state", sa.String(length=50), nullable=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("is_parked", sa.Boolean(), nullable=True),
            sa.Column("exit_time", sa.DateTime(), nullable=True),
            sa.Column("duration_minutes", sa.Integer(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("amount_paid", sa.Float(), nullable=True),
        )
        op.create_index("ix_detected_plates_tenant_id", "detected_plates", ["tenant_id"])
        op.create_index(
            "ix_detected_plates_tenant_time",
            "detected_plates",
            ["tenant_id", "timestamp"],
        )
        op.create_index(
            "ix_detected_plates_tenant_parked",
            "detected_plates",
            ["tenant_id", "is_parked"],
        )

    if "parking_sessions" not in tables:
        op.create_table(
            "parking_sessions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("start_time", sa.DateTime(), nullable=False),
            sa.Column("end_time", sa.DateTime(), nullable=True),
            sa.Column("plates_detected", sa.Integer(), nullable=True),
            sa.Column("spaces_used", sa.Integer(), nullable=True),
        )
        op.create_index("ix_parking_sessions_tenant_id", "parking_sessions", ["tenant_id"])
        op.create_index(
            "ix_parking_sessions_tenant_start",
            "parking_sessions",
            ["tenant_id", "start_time"],
        )

    if "parking_activity_log" not in tables:
        op.create_table(
            "parking_activity_log",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("tenant_id", sa.String(length=36), nullable=False),
            sa.Column("timestamp", sa.DateTime(), nullable=False),
            sa.Column("event_type", sa.String(length=50), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("plate_text", sa.String(length=20), nullable=True),
            sa.Column("space_id", sa.String(length=50), nullable=True),
            sa.Column("actor_user_id", sa.String(length=36), nullable=True),
        )
        op.create_index(
            "ix_parking_activity_log_tenant_id",
            "parking_activity_log",
            ["tenant_id"],
        )
        op.create_index(
            "ix_parking_activity_tenant_time",
            "parking_activity_log",
            ["tenant_id", "timestamp"],
        )


def downgrade() -> None:
    tables = _table_names()

    if "parking_activity_log" in tables:
        op.drop_index("ix_parking_activity_tenant_time", table_name="parking_activity_log")
        op.drop_index("ix_parking_activity_log_tenant_id", table_name="parking_activity_log")
        op.drop_table("parking_activity_log")

    if "parking_sessions" in tables:
        op.drop_index("ix_parking_sessions_tenant_start", table_name="parking_sessions")
        op.drop_index("ix_parking_sessions_tenant_id", table_name="parking_sessions")
        op.drop_table("parking_sessions")

    if "detected_plates" in tables:
        op.drop_index("ix_detected_plates_tenant_parked", table_name="detected_plates")
        op.drop_index("ix_detected_plates_tenant_time", table_name="detected_plates")
        op.drop_index("ix_detected_plates_tenant_id", table_name="detected_plates")
        op.drop_table("detected_plates")

    if "parking_spaces" in tables:
        op.drop_index("ix_parking_spaces_tenant_occupied", table_name="parking_spaces")
        op.drop_index("uq_parking_spaces_tenant_space", table_name="parking_spaces")
        op.drop_index("ix_parking_spaces_tenant_id", table_name="parking_spaces")
        op.drop_table("parking_spaces")

    if "vehicle_profiles" in tables:
        op.drop_index("uq_vehicle_profiles_tenant_plate", table_name="vehicle_profiles")
        op.drop_index("ix_vehicle_profiles_tenant_id", table_name="vehicle_profiles")
        op.drop_table("vehicle_profiles")

    if "cameras" in tables:
        cols = _column_names("cameras")
        if "gate_roi" in cols:
            op.drop_column("cameras", "gate_roi")
        if "role" in cols:
            op.drop_column("cameras", "role")
