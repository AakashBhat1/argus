'''Shared login throttling state (all workers see one count).

Usernames and source IPs are stored only as HMAC-SHA256 digests.

Revision ID: 20260924_0009
Revises: 20260924_0008
Create Date: 2026-09-24
'''

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260924_0009'
down_revision: Union[str, None] = '20260924_0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'login_failures',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('subject', sa.String(64), nullable=False),
        sa.Column('failed_at', sa.Float(), nullable=False),
    )
    op.create_index('ix_login_failures_subject_time', 'login_failures', ['subject', 'failed_at'])
    op.create_table(
        'login_lockouts',
        sa.Column('subject', sa.String(64), primary_key=True),
        sa.Column('locked_until', sa.Float(), nullable=False),
        sa.Column('lockout_level', sa.Integer(), nullable=False),
        sa.Column('last_failure_at', sa.Float(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('login_lockouts')
    op.drop_index('ix_login_failures_subject_time', table_name='login_failures')
    op.drop_table('login_failures')
