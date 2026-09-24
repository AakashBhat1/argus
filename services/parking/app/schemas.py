"""Parking service API schemas."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.utils import iso_utc
from argus_vision.schemas import CameraCalibrationSchema

ParkingCameraRole = Literal["gate_entry", "gate_exit", "parking"]


class CameraCreate(BaseModel):
    name: str = Field(..., max_length=255)
    location: str = Field(..., max_length=500)
    stream_url: str = Field(..., max_length=1000)
    resolution: str = "1280x720"
    fps: int = Field(default=30, ge=1, le=120)
    role: ParkingCameraRole = "parking"
    gate_roi: Optional[list] = None
    calibration: Optional[CameraCalibrationSchema] = None


class CameraUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    location: Optional[str] = Field(default=None, max_length=500)
    stream_url: Optional[str] = Field(default=None, max_length=1000)
    resolution: Optional[str] = None
    fps: Optional[int] = Field(default=None, ge=1, le=120)
    is_active: Optional[bool] = None
    role: Optional[ParkingCameraRole] = None
    gate_roi: Optional[list] = None
    calibration: Optional[CameraCalibrationSchema] = None


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
    calibration: Optional[dict] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    service: Literal["parking"] = "parking"

    @field_serializer("created_at", "updated_at")
    def _ser_dt(self, v: datetime) -> str:
        return iso_utc(v)



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
