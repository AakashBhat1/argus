"""Baseline parking schema (service split).

Parking owns its cameras, vehicle profiles, bays, plate reads, sessions,
activity log, alerts and the event outbox/inbox. Existing data is copied
from the surveillance database with scripts/migrate_parking_data.py.

Revision ID: 20260924_0001
Revises:
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260924_0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('cameras',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('location', sa.String(length=500), nullable=False),
    sa.Column('stream_url', sa.String(length=1000), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('resolution', sa.String(length=20), nullable=True),
    sa.Column('fps', sa.Integer(), nullable=True),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('gate_roi', sa.JSON(), nullable=True),
    sa.Column('calibration', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cameras_tenant_id'), 'cameras', ['tenant_id'], unique=False)
    op.create_table('inbox_events',
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=64), nullable=False),
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('received_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('event_id')
    )
    op.create_table('outbox_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('event_id', sa.String(length=64), nullable=False),
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('destination', sa.String(length=64), nullable=False),
    sa.Column('envelope', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('delivered_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_outbox_events_event_id'), 'outbox_events', ['event_id'], unique=False)
    op.create_index(op.f('ix_outbox_events_next_attempt_at'), 'outbox_events', ['next_attempt_at'], unique=False)
    op.create_index(op.f('ix_outbox_events_status'), 'outbox_events', ['status'], unique=False)
    op.create_index(op.f('ix_outbox_events_tenant_id'), 'outbox_events', ['tenant_id'], unique=False)
    op.create_table('parking_activity_log',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.Column('event_type', sa.String(length=50), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('plate_text', sa.String(length=20), nullable=True),
    sa.Column('space_id', sa.String(length=50), nullable=True),
    sa.Column('actor_user_id', sa.String(length=36), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_parking_activity_log_tenant_id'), 'parking_activity_log', ['tenant_id'], unique=False)
    op.create_index('ix_parking_activity_tenant_time', 'parking_activity_log', ['tenant_id', 'timestamp'], unique=False)
    op.create_table('parking_sessions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('start_time', sa.DateTime(), nullable=False),
    sa.Column('end_time', sa.DateTime(), nullable=True),
    sa.Column('plates_detected', sa.Integer(), nullable=True),
    sa.Column('spaces_used', sa.Integer(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_parking_sessions_tenant_id'), 'parking_sessions', ['tenant_id'], unique=False)
    op.create_index('ix_parking_sessions_tenant_start', 'parking_sessions', ['tenant_id', 'start_time'], unique=False)
    op.create_table('vehicle_profiles',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('plate_text', sa.String(length=20), nullable=False),
    sa.Column('profile_type', sa.String(length=20), nullable=True),
    sa.Column('owner_name', sa.String(length=255), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_vehicle_profiles_tenant_id'), 'vehicle_profiles', ['tenant_id'], unique=False)
    op.create_index('uq_vehicle_profiles_tenant_plate', 'vehicle_profiles', ['tenant_id', 'plate_text'], unique=True)
    op.create_table('alerts',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('camera_id', sa.String(length=36), nullable=True),
    sa.Column('tenant_id', sa.String(length=36), nullable=True),
    sa.Column('type', sa.String(length=100), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('trigger_condition', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=True),
    sa.Column('metadata', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_alerts_status', 'alerts', ['status'], unique=False)
    op.create_index(op.f('ix_alerts_tenant_id'), 'alerts', ['tenant_id'], unique=False)
    op.create_index('ix_alerts_tenant_time', 'alerts', ['tenant_id', 'timestamp'], unique=False)
    op.create_index('ix_alerts_timestamp', 'alerts', ['timestamp'], unique=False)
    op.create_table('detected_plates',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('plate_text', sa.String(length=20), nullable=False),
    sa.Column('vehicle_id', sa.String(length=36), nullable=True),
    sa.Column('camera_id', sa.String(length=36), nullable=True),
    sa.Column('track_id', sa.String(length=64), nullable=True),
    sa.Column('state', sa.String(length=50), nullable=True),
    sa.Column('timestamp', sa.DateTime(), nullable=False),
    sa.Column('is_parked', sa.Boolean(), nullable=True),
    sa.Column('exit_time', sa.DateTime(), nullable=True),
    sa.Column('duration_minutes', sa.Integer(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('amount_paid', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], ),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicle_profiles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_detected_plates_tenant_id'), 'detected_plates', ['tenant_id'], unique=False)
    op.create_index('ix_detected_plates_tenant_parked', 'detected_plates', ['tenant_id', 'is_parked'], unique=False)
    op.create_index('ix_detected_plates_tenant_time', 'detected_plates', ['tenant_id', 'timestamp'], unique=False)
    op.create_table('parking_spaces',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('space_id', sa.String(length=50), nullable=False),
    sa.Column('zone', sa.String(length=10), nullable=True),
    sa.Column('floor', sa.String(length=10), nullable=True),
    sa.Column('is_occupied', sa.Boolean(), nullable=True),
    sa.Column('vehicle_id', sa.String(length=36), nullable=True),
    sa.Column('entry_time', sa.DateTime(), nullable=True),
    sa.Column('camera_id', sa.String(length=36), nullable=True),
    sa.Column('polygon', sa.JSON(), nullable=True),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('detection_source', sa.String(length=20), nullable=False),
    sa.Column('last_state_change', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['camera_id'], ['cameras.id'], ),
    sa.ForeignKeyConstraint(['vehicle_id'], ['vehicle_profiles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_parking_spaces_camera_id'), 'parking_spaces', ['camera_id'], unique=False)
    op.create_index(op.f('ix_parking_spaces_tenant_id'), 'parking_spaces', ['tenant_id'], unique=False)
    op.create_index('ix_parking_spaces_tenant_occupied', 'parking_spaces', ['tenant_id', 'is_occupied'], unique=False)
    op.create_index('uq_parking_spaces_tenant_space', 'parking_spaces', ['tenant_id', 'space_id'], unique=True)


def downgrade() -> None:
    op.drop_index('uq_parking_spaces_tenant_space', table_name='parking_spaces')
    op.drop_index('ix_parking_spaces_tenant_occupied', table_name='parking_spaces')
    op.drop_index(op.f('ix_parking_spaces_tenant_id'), table_name='parking_spaces')
    op.drop_index(op.f('ix_parking_spaces_camera_id'), table_name='parking_spaces')
    op.drop_table('parking_spaces')
    op.drop_index('ix_detected_plates_tenant_time', table_name='detected_plates')
    op.drop_index('ix_detected_plates_tenant_parked', table_name='detected_plates')
    op.drop_index(op.f('ix_detected_plates_tenant_id'), table_name='detected_plates')
    op.drop_table('detected_plates')
    op.drop_index('ix_alerts_timestamp', table_name='alerts')
    op.drop_index('ix_alerts_tenant_time', table_name='alerts')
    op.drop_index(op.f('ix_alerts_tenant_id'), table_name='alerts')
    op.drop_index('ix_alerts_status', table_name='alerts')
    op.drop_table('alerts')
    op.drop_index('uq_vehicle_profiles_tenant_plate', table_name='vehicle_profiles')
    op.drop_index(op.f('ix_vehicle_profiles_tenant_id'), table_name='vehicle_profiles')
    op.drop_table('vehicle_profiles')
    op.drop_index('ix_parking_sessions_tenant_start', table_name='parking_sessions')
    op.drop_index(op.f('ix_parking_sessions_tenant_id'), table_name='parking_sessions')
    op.drop_table('parking_sessions')
    op.drop_index('ix_parking_activity_tenant_time', table_name='parking_activity_log')
    op.drop_index(op.f('ix_parking_activity_log_tenant_id'), table_name='parking_activity_log')
    op.drop_table('parking_activity_log')
    op.drop_index(op.f('ix_outbox_events_tenant_id'), table_name='outbox_events')
    op.drop_index(op.f('ix_outbox_events_status'), table_name='outbox_events')
    op.drop_index(op.f('ix_outbox_events_next_attempt_at'), table_name='outbox_events')
    op.drop_index(op.f('ix_outbox_events_event_id'), table_name='outbox_events')
    op.drop_table('outbox_events')
    op.drop_table('inbox_events')
    op.drop_index(op.f('ix_cameras_tenant_id'), table_name='cameras')
    op.drop_table('cameras')
