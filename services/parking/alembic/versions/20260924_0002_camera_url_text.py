'''Camera stream URLs are sealed at rest: widen the column for ciphertext.

Existing plaintext URLs are sealed by the service at startup once
CAMERA_SECRETS_KEY_FILE is configured (app.services.camera_secrets).

Revision ID: 20260924_0002
Revises: 20260924_0001
Create Date: 2026-09-24
'''

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260924_0002'
down_revision: Union[str, None] = '20260924_0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_cameras() -> bool:
    return 'cameras' in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_cameras():
        return
    with op.batch_alter_table('cameras') as batch_op:
        batch_op.alter_column('stream_url', type_=sa.Text(), existing_type=sa.String(1000), existing_nullable=False)


def downgrade() -> None:
    # Sealed values do not fit String(1000); only downgrade after unsealing.
    if not _has_cameras():
        return
    with op.batch_alter_table('cameras') as batch_op:
        batch_op.alter_column('stream_url', type_=sa.String(1000), existing_type=sa.Text(), existing_nullable=False)
