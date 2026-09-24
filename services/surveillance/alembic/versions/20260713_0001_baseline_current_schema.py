"""Baseline: current live schema (stamp-only).

Revision ID: 20260713_0001
Revises:
Create Date: 2026-07-13

Existing production databases already have the Argus core tables
(users, cameras, detections, alerts, roi_events, tracks, intent_events,
analytics_snapshots) created via ``create_all`` / prior deploy.

This revision is intentionally a **no-op** so that:
- ``alembic stamp head`` marks an existing DB at baseline without DDL
- ``alembic upgrade head`` is a no-op against the current schema
- subsequent revisions (e.g. parking tables) can build on a known head

Fresh environments that still use ``init_db()`` / ``create_all`` remain
compatible; they should also ``alembic stamp head`` after first boot
or run later migrations after the baseline stamp.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "20260713_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op baseline — live schema already present.
    pass


def downgrade() -> None:
    # Cannot tear down pre-baseline tables safely from this revision.
    pass
