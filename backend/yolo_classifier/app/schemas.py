from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from typing import Literal, Optional
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
    role: Literal['surveillance', 'gate_entry', 'gate_exit', 'parking'] = 'surveillance'
    gate_roi: Optional[list] = None


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    location: Optional[str] = None
    stream_url: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[int] = None
    is_active: Optional[bool] = None
    role: Optional[Literal['surveillance', 'gate_entry', 'gate_exit', 'parking']] = None
    gate_roi: Optional[list] = None


class CameraResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    location: str
    stream_url: str
    status: str
    resolution: str
    fps: int
    role: str
    gate_roi: Optional[list] = None
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


# ---- Smart Parking ---------------------------------------------------------


class ParkingStatsResponse(BaseModel):
    total: int
    occupied: int
    free: int
    available: int
    occupancy_pct: float
    plates_today: int


class ParkingSpaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    space_id: str
    zone: str
    floor: str
    is_occupied: bool
    vehicle_id: Optional[str] = None
    entry_time: Optional[datetime] = None
    plate_text: Optional[str] = None
    profile_type: Optional[str] = None
    camera_id: Optional[str] = None
    polygon: Optional[list[list[float]]] = None
    display_order: int = 0
    detection_source: str = 'manual'
    last_state_change: Optional[datetime] = None

    @field_serializer("entry_time", "last_state_change")
    def _ser_entry(self, v: Optional[datetime]) -> Optional[str]:
        return None if v is None else iso_utc(v)


class ParkingSlotInput(BaseModel):
    space_id: str = Field(..., min_length=1, max_length=50)
    polygon: list[tuple[float, float]]
    display_order: int = Field(default=0, ge=0)

    @field_validator('polygon')
    @classmethod
    def _validate_polygon(cls, polygon):
        if len(polygon) != 4:
            raise ValueError('polygon must contain exactly four points')
        if any(
            coordinate < 0.0 or coordinate > 1.0
            for point in polygon
            for coordinate in point
        ):
            raise ValueError('polygon coordinates must be within [0, 1]')
        return polygon


class ParkingSlotsReplaceRequest(BaseModel):
    slots: list[ParkingSlotInput] = Field(default_factory=list, max_length=500)

    @field_validator('slots')
    @classmethod
    def _unique_space_ids(cls, slots):
        ids = [slot.space_id for slot in slots]
        if len(ids) != len(set(ids)):
            raise ValueError('space_id values must be unique')
        return slots


class ParkingSlotPreviewRequest(BaseModel):
    camera_id: str
    slots: list[ParkingSlotInput] = Field(..., min_length=1, max_length=500)


class ParkingSlotPreviewReading(BaseModel):
    space_id: str
    occupied: bool
    score: float
    source: str


class ParkingSlotPreviewResponse(BaseModel):
    camera_id: str
    width: int
    height: int
    slots: list[ParkingSlotPreviewReading]


class ReleaseSpaceResponse(BaseModel):
    space_id: str
    plate_text: Optional[str] = None
    duration_minutes: int
    amount_paid: float


class DetectedPlateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    plate_text: str
    vehicle_id: Optional[str] = None
    camera_id: Optional[str] = None
    track_id: Optional[str] = None
    state: Optional[str] = None
    timestamp: datetime
    is_parked: bool
    exit_time: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    confidence: float
    amount_paid: float

    @field_serializer("timestamp", "exit_time")
    def _ser_plate_dt(self, v: Optional[datetime]) -> Optional[str]:
        return None if v is None else iso_utc(v)


class ParkingActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    timestamp: datetime
    event_type: str
    description: str
    plate_text: Optional[str] = None
    space_id: Optional[str] = None
    actor_user_id: Optional[str] = None

    @field_serializer("timestamp")
    def _ser_act(self, v: datetime) -> str:
        return iso_utc(v)


class ParkingChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    # When true, use command persona (admin only — enforced in router)
    command: bool = False


class ParkingChatResponse(BaseModel):
    role: str
    mode: str
    content: str
    command: Optional[dict] = None
    executed: bool = False
    result: Optional[dict] = None
    error: Optional[str] = None
