"""Settings shared by every service that runs the vision pipeline.

Each service defines its own ``Settings`` class that *extends*
``VisionSettings`` and registers its settings getter with :func:`configure`.
Vision modules call :func:`get_settings` and therefore see the owning
service's configuration (including test overrides), while still working
standalone with defaults.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ALLOWED_CLASSES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "bus",
    "truck",
]


class VisionSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Directory that relative model/config paths resolve against. Services
    # override this with their own root.
    SERVICE_DIR: str = os.getcwd()

    # -- OpenVINO inference ---------------------------------------------------
    # Path to the OpenVINO IR model (.xml), relative to SERVICE_DIR.
    OPENVINO_MODEL_PATH: str = "models/yolov8n.xml"
    # CPU | GPU | NPU | AUTO
    OPENVINO_DEVICE: str = "AUTO"
    # The detector issues synchronous single-frame requests, so LATENCY is the
    # right hint; THROUGHPUT on Intel iGPUs makes each blocking call far slower.
    OPENVINO_PERFORMANCE_HINT: str = "LATENCY"
    # Informational; the converted model file determines real precision.
    OPENVINO_PRECISION: str = "FP16"
    YOLO_CONFIDENCE: float = 0.35
    # Only used for legacy YOLOv8 models; YOLO26 is end-to-end (no NMS).
    YOLO_NMS_IOU: float = 0.5
    ALLOWED_CLASSES: list[str] = list(DEFAULT_ALLOWED_CLASSES)
    YOLO_CLASS_CONFIDENCE_THRESHOLDS: dict[str, float] = {
        "person": 0.35,
        "bicycle": 0.35,
        "car": 0.35,
        "motorcycle": 0.35,
        "bus": 0.35,
        "truck": 0.35,
    }
    YOLO_LOG_RAW_DETECTIONS: bool = False
    YOLO_RAW_DETECTIONS_MAX_LOG: int = 20

    # -- ROI / intrusion ------------------------------------------------------
    ROI_ENABLED: bool = True
    # Relative to SERVICE_DIR.
    ROI_ZONES_CONFIG_PATH: str = "intrusion_monitor/zones_config.json"
    ROI_REFERENCE_WIDTH: int = 960
    ROI_REFERENCE_HEIGHT: int = 544
    ROI_DEFAULT_DWELL_SEC: float = 5.0
    ROI_ALERT_COOLDOWN_SEC: float = 10.0
    ROI_INTRUDER_CLASSES: list[str] = ["person"]
    # "foot" (bottom-centre, correct for ground-plane zones) or "center".
    ROI_ANCHOR: str = "foot"
    ROI_TRACK_GRACE_SEC: float = 2.0
    ROI_INCIDENT_HOLDDOWN_SEC: float = 30.0

    # -- Geometry / tracking --------------------------------------------------
    CAMERA_DEFAULT_HFOV_DEG: float = 84.0
    TRACKER_MAX_AGE: int = 30
    TRACKER_N_INIT: int = 3

    # -- Stream processing ----------------------------------------------------
    MAX_STREAMS: int = 20
    FRAME_SKIP: int = 6
    BATCH_ENABLED: bool = True
    BATCH_MAX_SIZE: int = 8
    BATCH_TIMEOUT_MS: int = 20
    # OpenVINO releases the GIL, so threads give real parallelism.
    INFERENCE_WORKERS: int = 2
    INFERENCE_QUEUE_MAX: int = 64
    ADAPTIVE_FPS_ENABLED: bool = True
    TARGET_LATENCY_MS: int = 100

    # -- Camera source policy (SSRF; see argus_common.net) -------------------
    CAMERA_NETWORK_ALLOWLIST: list[str] = []
    STREAM_HOST_ALLOWLIST: list[str] = ["mediamtx:8554"]
    ALLOW_HTTP_STREAMS: bool = False
    # Deprecated: RFC 1918 ranges + http. Loopback and metadata stay blocked.
    ALLOW_PRIVATE_STREAM_URLS: bool = False
    ALLOW_LOCAL_CAPTURE_DEVICES: bool = True
    # Directories video://<file> sources resolve into. Empty = SERVICE_DIR/video.
    VIDEO_SOURCE_DIRS: list[str] = []

    # -- MediaMTX -------------------------------------------------------------
    MEDIAMTX_ENABLED: bool = True
    MEDIAMTX_API_BASE_URL: str = "http://mediamtx:9997"
    MEDIAMTX_REQUEST_TIMEOUT_SECONDS: float = 3.0
    MEDIAMTX_API_USERNAME: str | None = None
    MEDIAMTX_API_PASSWORD: str | None = None
    # Single ingest: analytics reads the MediaMTX path instead of opening a
    # second RTSP session on the camera.
    MEDIAMTX_SINGLE_INGEST: bool = True
    MEDIAMTX_RTSP_READ_BASE_URL: str = "rtsp://mediamtx:8554"
    MEDIAMTX_READ_USERNAME: str | None = None
    MEDIAMTX_READ_PASSWORD: str | None = None

    def resolve_path(self, value: str | os.PathLike) -> Path:
        """Resolve ``value`` against SERVICE_DIR unless it is absolute."""
        path = Path(value)
        return path if path.is_absolute() else Path(self.SERVICE_DIR) / path


_provider: Optional[Callable[[], VisionSettings]] = None


@lru_cache
def _default_settings() -> VisionSettings:
    return VisionSettings()


def configure(provider: Callable[[], VisionSettings]) -> None:
    """Register the owning service's settings getter."""
    global _provider
    _provider = provider


def get_settings() -> VisionSettings:
    return _provider() if _provider is not None else _default_settings()
