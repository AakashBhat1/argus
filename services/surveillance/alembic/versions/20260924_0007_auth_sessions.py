'''Dashboard sessions: rotating refresh tokens (hashes only).

Revision ID: 20260924_0007
Revises: 20260924_0006
Create Date: 2026-09-24
'''

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260924_0007'
down_revision: Union[str, None] = '20260924_0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'auth_sessions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('family_id', sa.String(36), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('family_expires_at', sa.DateTime(), nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('user_agent', sa.String(200), nullable=True),
    )
    op.create_index('ix_auth_sessions_family_id', 'auth_sessions', ['family_id'])
    op.create_index('ix_auth_sessions_user_id', 'auth_sessions', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_auth_sessions_user_id', table_name='auth_sessions')
    op.drop_index('ix_auth_sessions_family_id', table_name='auth_sessions')
    op.drop_table('auth_sessions')
