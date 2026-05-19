from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from typing import Optional
from app.models import UserRole
from app.utils import iso_utc


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None
    role: Optional[str] = None
    tenant_id: Optional[str] = None


class UserCreate(BaseModel):
    username: str = Field(..., max_length=255)
    password: str = Field(..., max_length=255)
    role: str = UserRole.OPERATOR.value


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    role: str
    tenant_id: str
    is_active: bool
    created_at: datetime

    @field_serializer("created_at")
    def _ser_created_at(self, v: datetime) -> str:
        return iso_utc(v)


class CameraCreate(BaseModel):
    name: str = Field(..., max_length=255)
    location: str = Field(..., max_length=500)
    stream_url: str = Field(..., max_length=1000)
    resolution: str = "1280x720"
    fps: int = 30


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    stream_url: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[int] = None
    is_active: Optional[bool] = None


class CameraResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    location: str
    stream_url: str
    status: str
    resolution: str
    fps: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _ser_dt(self, v: datetime) -> str:
        return iso_utc(v)


class DetectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    camera_id: str
    object_id: int
    class_label: str
    confidence: float
    bbox_x: float
    bbox_y: float
    bbox_w: float
    bbox_h: float
    timestamp: datetime
    frame_number: Optional[int] = None

    @field_serializer("timestamp")
    def _ser_ts(self, v: datetime) -> str:
        return iso_utc(v)


class DetectionCreate(BaseModel):
    camera_id: str
    object_id: int
    class_label: str
    confidence: float
    bbox_x: float
    bbox_y: float
    bbox_w: float
    bbox_h: float
    frame_number: Optional[int] = None


class AlertCreate(BaseModel):
    camera_id: str
    type: str
    severity: str = "low"
    trigger_condition: Optional[str] = None
    description: Optional[str] = None


class AlertUpdate(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    description: Optional[str] = None


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    camera_id: str
    type: str
    severity: str
    status: str
    trigger_condition: Optional[str] = None
    description: Optional[str] = None
    timestamp: datetime
    resolved_at: Optional[datetime] = None

    @field_serializer("timestamp", "resolved_at")
    def _ser_dt(self, v: Optional[datetime]) -> Optional[str]:
        return None if v is None else iso_utc(v)


class AnalyticsQuery(BaseModel):
    camera_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    class_label: Optional[str] = None


class AnalyticsResponse(BaseModel):
    total_detections: int
    unique_objects: int
    class_distribution: dict[str, int]
    avg_confidence: float
    detections_per_minute: float
    peak_count: int
    time_series: list[dict]


class StreamStatus(BaseModel):
    camera_id: str
    is_running: bool
    fps: float
    frame_count: int
    uptime_seconds: float


class SystemStats(BaseModel):
    active_streams: int
    total_cameras: int
    total_detections_today: int
    active_alerts: int
    avg_fps: float
    inference_device: str
