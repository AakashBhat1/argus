'''Admin audit log (who changed what).

The surveillance baseline is stamp-only and init_db() may already have
created the table, so creation is guarded by an existence check.

Revision ID: 20260924_0010
Revises: 20260924_0009
Create Date: 2026-11-08
'''

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260924_0010'
down_revision: Union[str, None] = '20260924_0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table('audit_events'):
        return
    op.create_table(
        'audit_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('tenant_id', sa.String(36), nullable=False),
        sa.Column('actor', sa.String(160), nullable=False),
        sa.Column('actor_id', sa.String(64), nullable=True),
        sa.Column('action', sa.String(64), nullable=False),
        sa.Column('target_type', sa.String(40), nullable=True),
        sa.Column('target_id', sa.String(64), nullable=True),
        sa.Column('details', sa.JSON(), nullable=True),
        sa.Column('source_ip', sa.String(64), nullable=True),
    )
    op.create_index('ix_audit_events_tenant_created', 'audit_events', ['tenant_id', 'created_at', 'id'])
    op.create_index('ix_audit_events_action', 'audit_events', ['action'])


def downgrade() -> None:
    if not _has_table('audit_events'):
        return
    op.drop_index('ix_audit_events_action', table_name='audit_events')
    op.drop_index('ix_audit_events_tenant_created', table_name='audit_events')
    op.drop_table('audit_events')
