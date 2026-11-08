"""ROI zones move from one global JSON file into a tenant-scoped table.

Existing zones are imported explicitly, because a shared file cannot say which
tenant owns them: python -m app.cli.import_zones --tenant-id <id>

The surveillance baseline is stamp-only and init_db() may already have
created the table, so creation is guarded by an existence check.

Revision ID: 20260924_0011
Revises: 20260924_0010
Create Date: 2026-11-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260924_0011'
down_revision: Union[str, None] = '20260924_0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table('roi_zones'):
        return
    op.create_table(
        'roi_zones',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('points', sa.JSON(), nullable=False),
        sa.Column('threshold_sec', sa.Float(), nullable=False),
        sa.Column('color', sa.JSON(), nullable=False),
        sa.Column('camera_ids', sa.JSON(), nullable=True),
        sa.Column('zone_type', sa.String(20), nullable=False),
        sa.Column('armed_schedule', sa.JSON(), nullable=False),
        sa.Column('allowed_classes', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_roi_zones_tenant_id', 'roi_zones', ['tenant_id'])


def downgrade() -> None:
    if not _has_table('roi_zones'):
        return
    op.drop_index('ix_roi_zones_tenant_id', table_name='roi_zones')
    op.drop_table('roi_zones')
