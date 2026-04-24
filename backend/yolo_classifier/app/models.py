import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, String, Float, Integer, DateTime, ForeignKey,
    Boolean, Text, Index, JSON,
)
from sqlalchemy.orm import relationship
import enum

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


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    OPERATOR = "operator"


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    username = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), default=UserRole.OPERATOR.value)
    tenant_id = Column(String(36), default="1", index=True)
    created_at = Column(DateTime, default=utc_now)
    is_active = Column(Boolean, default=True)


class Camera(Base):
    __tablename__ = "cameras"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    location = Column(String(500), nullable=False)
    stream_url = Column(String(1000), nullable=False)
    tenant_id = Column(String(36), default="1", index=True)
    status = Column(String(20), default=CameraStatus.INACTIVE.value)
    resolution = Column(String(20), default="1280x720")
    fps = Column(Integer, default=30)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)
    is_active = Column(Boolean, default=True)

    detections = relationship("Detection", back_populates="camera", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="camera", cascade="all, delete-orphan")
    roi_events = relationship("RoiEvent", back_populates="camera", cascade="all, delete-orphan")
    analytics_snapshots = relationship("AnalyticsSnapshot", back_populates="camera", cascade="all, delete-orphan")


class Detection(Base):
    __tablename__ = "detections"
    __table_args__ = (
        Index("ix_detections_timestamp", "timestamp"),
        Index("ix_detections_camera_class", "camera_id", "class_label"),
        Index("ix_detections_tenant_time", "tenant_id", "timestamp"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=False)
    tenant_id = Column(String(36), default="1", index=True)
    object_id = Column(Integer, nullable=False)
    class_label = Column(String(100), nullable=False)
    confidence = Column(Float, nullable=False)
    bbox_x = Column(Float, nullable=False)
    bbox_y = Column(Float, nullable=False)
    bbox_w = Column(Float, nullable=False)
    bbox_h = Column(Float, nullable=False)
    timestamp = Column(DateTime, default=utc_now, nullable=False)
    frame_number = Column(Integer)
    metadata_ = Column("metadata", JSON, default=dict)

    camera = relationship("Camera", back_populates="detections")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_status", "status"),
        Index("ix_alerts_timestamp", "timestamp"),
        Index("ix_alerts_tenant_time", "tenant_id", "timestamp"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=False)
    tenant_id = Column(String(36), default="1", index=True)
    type = Column(String(100), nullable=False)
    severity = Column(String(20), default=AlertSeverity.LOW.value)
    status = Column(String(20), default=AlertStatus.ACTIVE.value)
    trigger_condition = Column(Text)
    description = Column(Text)
    timestamp = Column(DateTime, default=utc_now, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)

    camera = relationship("Camera", back_populates="alerts")


class RoiEvent(Base):
    __tablename__ = "roi_events"
    __table_args__ = (
        Index("ix_roi_events_tenant_time", "tenant_id", "timestamp"),
        Index("ix_roi_events_camera_time", "camera_id", "timestamp"),
        Index("ix_roi_events_intrusion", "tenant_id", "has_intrusion", "timestamp"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(36), default="1", index=True)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=False)
    timestamp = Column(DateTime, default=utc_now, nullable=False)
    event_type = Column(String(50), default="movement")
    zone = Column(String(255), nullable=True)
    confidence = Column(Float, nullable=True)
    frame_number = Column(Integer)
    has_intrusion = Column(Boolean, default=False, nullable=False)
    has_movement = Column(Boolean, default=False, nullable=False)
    classes = Column(JSON, default=dict, nullable=False)
    raw_event = Column(JSON, default=dict, nullable=False)
    metadata_ = Column("metadata", JSON, default=dict)

    camera = relationship("Camera", back_populates="roi_events")


class IntentType(str, enum.Enum):
    PASSING_THROUGH = "passing_through"
    LOITERING = "loitering"
    SURVEILLANCE = "surveillance"
    INTRUSION = "intrusion"
    DELIVERY = "delivery"
    PATROL = "patrol"
    UNKNOWN = "unknown"


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (
        Index("ix_tracks_camera_time", "camera_id", "started_at"),
        Index("ix_tracks_tenant_time", "tenant_id", "started_at"),
        Index("ix_tracks_object_id", "camera_id", "object_id"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=False)
    tenant_id = Column(String(36), default="1", index=True)
    object_id = Column(Integer, nullable=False)
    class_label = Column(String(100), nullable=False)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=False)
    duration_sec = Column(Float, nullable=False)
    total_distance = Column(Float, default=0.0)
    avg_speed = Column(Float, default=0.0)
    max_speed = Column(Float, default=0.0)
    direction_changes = Column(Integer, default=0)
    stationary_ratio = Column(Float, default=0.0)
    bbox_coverage = Column(Float, default=0.0)
    entry_point = Column(JSON, default=list)
    exit_point = Column(JSON, default=list)
    roi_zones_visited = Column(JSON, default=list)
    had_intrusion = Column(Boolean, default=False)
    trajectory = Column(JSON, default=list)
    feature_vector = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utc_now)

    intent_events = relationship("IntentEvent", back_populates="track", cascade="all, delete-orphan")


class IntentEvent(Base):
    __tablename__ = "intent_events"
    __table_args__ = (
        Index("ix_intent_events_camera_time", "camera_id", "timestamp"),
        Index("ix_intent_events_tenant_intent", "tenant_id", "intent_type"),
        Index("ix_intent_events_intent_conf", "intent_type", "confidence"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    track_id = Column(String(36), ForeignKey("tracks.id"), nullable=False)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=False)
    tenant_id = Column(String(36), default="1", index=True)
    object_id = Column(Integer, nullable=False)
    class_label = Column(String(100), nullable=False)
    intent_type = Column(String(50), nullable=False)
    confidence = Column(Float, nullable=False)
    reasoning = Column(Text, nullable=True)
    classifier_version = Column(String(50), default="rule_v1")
    timestamp = Column(DateTime, default=utc_now, nullable=False)
    features = Column(JSON, default=dict)
    metadata_ = Column("metadata", JSON, default=dict)

    track = relationship("Track", back_populates="intent_events")


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        Index("ix_analytics_camera_period", "camera_id", "period_start"),
        Index("ix_analytics_tenant_period", "tenant_id", "period_start"),
    )

    id = Column(String(36), primary_key=True, default=generate_uuid)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=False)
    tenant_id = Column(String(36), default="1", index=True)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    total_detections = Column(Integer, default=0)
    unique_objects = Column(Integer, default=0)
    class_counts = Column(JSON, default=dict)
    avg_confidence = Column(Float, default=0.0)
    peak_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utc_now)

    camera = relationship("Camera", back_populates="analytics_snapshots")
