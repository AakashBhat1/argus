'''ParkBot actions wait for explicit admin confirmation.

Revision ID: 20260924_0004
Revises: 20260924_0003
Create Date: 2026-11-08
'''

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260924_0004'
down_revision: Union[str, None] = '20260924_0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table('parkbot_proposals'):
        return
    op.create_table(
        'parkbot_proposals',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('actor_id', sa.String(64), nullable=False),
        sa.Column('command', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('consumed_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_parkbot_proposals_tenant_id', 'parkbot_proposals', ['tenant_id'])


def downgrade() -> None:
    if not _has_table('parkbot_proposals'):
        return
    op.drop_index('ix_parkbot_proposals_tenant_id', table_name='parkbot_proposals')
    op.drop_table('parkbot_proposals')
