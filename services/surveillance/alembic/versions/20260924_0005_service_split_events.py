'''Service split: cross-service events and externally sourced alerts.

- alerts.camera_id becomes nullable and alerts gain ``source`` /
  ``source_ref`` so alerts raised by the parking service (about cameras this
  database does not own) can be recorded on the security console.
- outbox_events / inbox_events back the transactional outbox and the
  idempotent inbox (argus_common.events).

Revision ID: 20260924_0005
Revises: 20260904_0004
Create Date: 2026-09-24
'''

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260924_0005'
down_revision: Union[str, None] = '20260904_0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column['name'] for column in inspector.get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if 'alerts' in tables:
        columns = _columns('alerts')
        with op.batch_alter_table('alerts') as batch_op:
            batch_op.alter_column('camera_id', existing_type=sa.String(36), nullable=True)
            if 'source' not in columns:
                batch_op.add_column(
                    sa.Column('source', sa.String(32), nullable=False, server_default='surveillance')
                )
            if 'source_ref' not in columns:
                batch_op.add_column(sa.Column('source_ref', sa.String(64), nullable=True))

    if 'outbox_events' not in tables:
        op.create_table(
            'outbox_events',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('event_id', sa.String(64), nullable=False),
            sa.Column('event_type', sa.String(100), nullable=False),
            sa.Column('tenant_id', sa.String(36), nullable=False),
            sa.Column('destination', sa.String(64), nullable=False),
            sa.Column('envelope', sa.JSON(), nullable=False),
            sa.Column('status', sa.String(16), nullable=False, server_default='pending'),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('delivered_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_outbox_events_event_id', 'outbox_events', ['event_id'])
        op.create_index('ix_outbox_events_tenant_id', 'outbox_events', ['tenant_id'])
        op.create_index('ix_outbox_events_status', 'outbox_events', ['status'])
        op.create_index('ix_outbox_events_next_attempt_at', 'outbox_events', ['next_attempt_at'])

    if 'inbox_events' not in tables:
        op.create_table(
            'inbox_events',
            sa.Column('event_id', sa.String(64), primary_key=True),
            sa.Column('source', sa.String(64), nullable=False),
            sa.Column('event_type', sa.String(100), nullable=False),
            sa.Column('tenant_id', sa.String(36), nullable=False),
            sa.Column('received_at', sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    tables = _tables()
    if 'inbox_events' in tables:
        op.drop_table('inbox_events')
    if 'outbox_events' in tables:
        for index in (
            'ix_outbox_events_next_attempt_at',
            'ix_outbox_events_status',
            'ix_outbox_events_tenant_id',
            'ix_outbox_events_event_id',
        ):
            op.drop_index(index, table_name='outbox_events')
        op.drop_table('outbox_events')
    if 'alerts' in tables:
        columns = _columns('alerts')
        with op.batch_alter_table('alerts') as batch_op:
            if 'source_ref' in columns:
                batch_op.drop_column('source_ref')
            if 'source' in columns:
                batch_op.drop_column('source')
        # camera_id stays nullable: rows written by peers have no camera and
        # cannot be given one retroactively.
