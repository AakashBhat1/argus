"""Parking service data model.

Parking owns its cameras (gate and lot cameras), vehicle profiles, bays,
plate reads, sessions and its own alerts. Nothing here is shared with the
surveillance database; cross-service facts travel as events (outbox/inbox).
"""

import enum
import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text,
)
from sqlalchemy.orm import relationship

from argus_common.secretbox import SealedString
from app.services.camera_secrets import STREAM_URL_AAD, camera_secret_box

from argus_common.events import InboxEventMixin, OutboxEventMixin
from app.database import Base
from app.utils import utc_now


def generate_uuid():
    return str(uuid.uuid4())


class CameraStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class AlertSeverity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, enum.Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


PARKING_CAMERA_ROLES = ("gate_entry", "gate_exit", "parking")


class Camera(Base):
    """A parking-side camera: gate (plate OCR) or lot (bay occupancy)."""

    __tablename__ = "cameras"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    location = Column(String(500), nullable=False)
    # Sealed at rest: stream URLs carry the camera's credentials.
    stream_url = Column(SealedString(camera_secret_box, STREAM_URL_AAD), nullable=False)
    tenant_id = Column(String(36), default="1", index=True)
    status = Column(String(20), default=CameraStatus.INACTIVE.value)
    resolution = Column(String(20), default="1280x720")
    fps = Column(Integer, default=30)
    # gate_entry | gate_exit | parking
    role = Column(String(20), default="parking", nullable=False)
    # OCR trigger polygon: list of {x,y} (normalized 0..1 or pixels)
    gate_roi = Column(JSON, nullable=True)
    # Camera geometry calibration (see argus_vision.geometry).
    calibration = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    is_active = Column(Boolean, default=True)


class Alert(Base):
    """Parking-side alert (also forwarded to surveillance as an event)."""

    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_status", "status"),
        Index("ix_alerts_timestamp", "timestamp"),
        Index("ix_alerts_tenant_time", "tenant_id", "timestamp"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=True)
    tenant_id = Column(String(36), default="1", index=True)
    type = Column(String(100), nullable=False)
    severity = Column(String(20), default=AlertSeverity.LOW.value)
    status = Column(String(20), default=AlertStatus.ACTIVE.value)
    trigger_condition = Column(Text)
    description = Column(Text)
    timestamp = Column(DateTime, default=utc_now, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)


class OutboxEvent(OutboxEventMixin, Base):
    pass


class InboxEvent(InboxEventMixin, Base):
    pass


class VehicleProfile(Base):
    __tablename__ = "vehicle_profiles"
    __table_args__ = (
        Index("uq_vehicle_profiles_tenant_plate", "tenant_id", "plate_text", unique=True),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(36), default="1", index=True, nullable=False)
    plate_text = Column(String(20), nullable=False)
    profile_type = Column(String(20), default="normal")  # normal, vip, blacklist
    owner_name = Column(String(255), default="Visitor")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    parking_spot = relationship("ParkingSpace", back_populates="vehicle", uselist=False)
    detections = relationship("DetectedPlate", back_populates="profile")


class ParkingSpace(Base):
    __tablename__ = "parking_spaces"
    __table_args__ = (
        Index("uq_parking_spaces_tenant_space", "tenant_id", "space_id", unique=True),
        Index("ix_parking_spaces_tenant_occupied", "tenant_id", "is_occupied"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(36), default="1", index=True, nullable=False)
    space_id = Column(String(50), nullable=False)  # e.g. "A-01", "F2-08" (unique per tenant)
    zone = Column(String(10), default="A")  # A, B, C
    floor = Column(String(10), default="G")  # G, 1, 2
    is_occupied = Column(Boolean, default=False)
    # FK on surrogate id, NOT plate_text — avoids cross-tenant natural-key collisions
    vehicle_id = Column(String(36), ForeignKey("vehicle_profiles.id"), nullable=True)
    entry_time = Column(DateTime, nullable=True)
    camera_id = Column(String(36), ForeignKey('cameras.id'), nullable=True, index=True)
    polygon = Column(JSON, nullable=True)
    display_order = Column(Integer, default=0, nullable=False)
    detection_source = Column(String(20), default='manual', nullable=False)
    last_state_change = Column(DateTime, nullable=True)

    vehicle = relationship("VehicleProfile", back_populates="parking_spot")


class DetectedPlate(Base):
    __tablename__ = "detected_plates"
    __table_args__ = (
        Index("ix_detected_plates_tenant_time", "tenant_id", "timestamp"),
        Index("ix_detected_plates_tenant_parked", "tenant_id", "is_parked"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(36), default="1", index=True, nullable=False)
    plate_text = Column(String(20), nullable=False)
    vehicle_id = Column(String(36), ForeignKey("vehicle_profiles.id"), nullable=True)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=True)
    track_id = Column(String(64), nullable=True)
    state = Column(String(50), nullable=True)
    timestamp = Column(DateTime, default=utc_now, nullable=False)
    is_parked = Column(Boolean, default=False)
    exit_time = Column(DateTime, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    confidence = Column(Float, default=0.0)
    amount_paid = Column(Float, default=0.0)

    profile = relationship("VehicleProfile", back_populates="detections")


class ParkingSession(Base):
    __tablename__ = "parking_sessions"
    __table_args__ = (Index("ix_parking_sessions_tenant_start", "tenant_id", "start_time"),)

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(36), default="1", index=True, nullable=False)
    start_time = Column(DateTime, default=utc_now, nullable=False)
    end_time = Column(DateTime, nullable=True)
    plates_detected = Column(Integer, default=0)
    spaces_used = Column(Integer, default=0)


class ParkingActivityLog(Base):
    __tablename__ = "parking_activity_log"
    __table_args__ = (Index("ix_parking_activity_tenant_time", "tenant_id", "timestamp"),)

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(36), default="1", index=True, nullable=False)
    timestamp = Column(DateTime, default=utc_now, nullable=False)
    event_type = Column(String(50), nullable=False)  # entry, exit, profile_change, command
    description = Column(Text, nullable=False)
    plate_text = Column(String(20), nullable=True)
    space_id = Column(String(50), nullable=True)
    actor_user_id = Column(String(36), nullable=True)

