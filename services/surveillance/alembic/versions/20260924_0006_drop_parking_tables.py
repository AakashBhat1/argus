'''Parking data moved to the parking service: drop its tables here.

Run ``services/parking/scripts/migrate_parking_data.py`` first. If any
parking table still contains rows this migration refuses to run unless
ARGUS_PARKING_DATA_MIGRATED=1 is set, so data cannot be dropped by
accident. Downgrade recreates the (empty) tables.

Revision ID: 20260924_0006
Revises: 20260924_0005
Create Date: 2026-09-24
'''

import importlib.util
import os
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '20260924_0006'
down_revision: Union[str, None] = '20260924_0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Children before parents (foreign keys).
PARKING_TABLES = (
    'parking_activity_log',
    'parking_sessions',
    'detected_plates',
    'parking_spaces',
    'vehicle_profiles',
)


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    present = [name for name in PARKING_TABLES if name in tables]
    bind = op.get_bind()
    populated = [
        name for name in present
        if bind.execute(sa.text(f'SELECT COUNT(*) FROM "{name}"')).scalar()
    ]
    if populated and os.getenv('ARGUS_PARKING_DATA_MIGRATED') != '1':
        raise RuntimeError(
            'Parking tables still hold data: '
            + ', '.join(populated)
            + '. Copy it to the parking service with '
            'services/parking/scripts/migrate_parking_data.py, then re-run with '
            'ARGUS_PARKING_DATA_MIGRATED=1.'
        )
    for name in present:
        op.drop_table(name)


def _load_sibling(filename: str):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def downgrade() -> None:
    # Recreate the empty parking tables exactly as the original revisions did.
    _load_sibling('20260713_0002_parking_tables_and_camera_gate.py').upgrade()
    _load_sibling('20260814_0003_parking_slot_geometry.py').upgrade()
