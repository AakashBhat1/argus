'''Add camera-owned parking slot geometry.

Revision ID: 20260814_0003
Revises: 20260713_0002
Create Date: 2026-08-14
'''

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260814_0003'
down_revision: Union[str, None] = '20260713_0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column['name'] for column in inspector.get_columns(table)}


def _index_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {index['name'] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    if 'parking_spaces' not in _table_names():
        return

    columns = _column_names('parking_spaces')
    camera_fk = (
        sa.ForeignKey('cameras.id', name='fk_parking_spaces_camera_id') if 'cameras' in _table_names() else None
    )
    additions = []
    if 'camera_id' not in columns:
        additions.append(
            sa.Column('camera_id', sa.String(length=36), camera_fk, nullable=True)
        )
    if 'polygon' not in columns:
        additions.append(sa.Column('polygon', sa.JSON(), nullable=True))
    if 'display_order' not in columns:
        additions.append(
            sa.Column('display_order', sa.Integer(), server_default='0', nullable=False)
        )
    if 'detection_source' not in columns:
        additions.append(
            sa.Column(
                'detection_source',
                sa.String(length=20),
                server_default='manual',
                nullable=False,
            )
        )
    if 'last_state_change' not in columns:
        additions.append(sa.Column('last_state_change', sa.DateTime(), nullable=True))

    if additions:
        with op.batch_alter_table('parking_spaces') as batch_op:
            for column in additions:
                batch_op.add_column(column)

    if 'ix_parking_spaces_camera_id' not in _index_names('parking_spaces'):
        op.create_index(
            'ix_parking_spaces_camera_id',
            'parking_spaces',
            ['camera_id'],
        )


def downgrade() -> None:
    if 'parking_spaces' not in _table_names():
        return

    if 'ix_parking_spaces_camera_id' in _index_names('parking_spaces'):
        op.drop_index('ix_parking_spaces_camera_id', table_name='parking_spaces')

    columns = _column_names('parking_spaces')
    removable = [
        name
        for name in (
            'last_state_change',
            'detection_source',
            'display_order',
            'polygon',
            'camera_id',
        )
        if name in columns
    ]
    if removable:
        with op.batch_alter_table('parking_spaces') as batch_op:
            for name in removable:
                batch_op.drop_column(name)
