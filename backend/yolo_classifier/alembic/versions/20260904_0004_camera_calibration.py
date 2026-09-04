'''Add camera geometry calibration.

Revision ID: 20260904_0004
Revises: 20260814_0003
Create Date: 2026-09-04
'''

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260904_0004'
down_revision: Union[str, None] = '20260814_0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column['name'] for column in inspector.get_columns(table)}


def upgrade() -> None:
    if 'cameras' not in sa.inspect(op.get_bind()).get_table_names():
        return
    if 'calibration' not in _column_names('cameras'):
        with op.batch_alter_table('cameras') as batch_op:
            batch_op.add_column(sa.Column('calibration', sa.JSON(), nullable=True))


def downgrade() -> None:
    if 'calibration' in _column_names('cameras'):
        with op.batch_alter_table('cameras') as batch_op:
            batch_op.drop_column('calibration')
