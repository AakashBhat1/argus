import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "surveillance.db")

DEFAULT_ALLOWED_CLASSES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "bus",
    "truck",
]


class Settings(BaseSettings):
    """
    Configuration for the standalone YOLO classifier service.

    Uses Pydantic v2-style config so we can safely ignore any extra
    environment variables coming from the larger stack (e.g. Postgres
    and Redis URLs) while running this component on its own.
    """

    # pydantic-settings v2 configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    APP_NAME: str = "AI Surveillance System"
    DEBUG: bool = False

    # Local SQLite DB for the classifier-only service
    DATABASE_URL: str = f"sqlite+aiosqlite:///{DB_PATH}"

    # Optional extra URLs for compatibility with larger stack configs.
    # These exist in your environment/.env for the full system; we
    # declare them here so Pydantic doesn't raise "extra fields" errors.
    DATABASE_URL_SYNC: str | None = None
    REDIS_URL: str | None = None

    # -- OpenVINO Inference Settings ------------------------------------------

    # Path to the OpenVINO IR model (.xml). Relative to backend/ directory.
    OPENVINO_MODEL_PATH: str = "models/yolo26n_int8.xml"

    # Target device: CPU | GPU | NPU | AUTO
    # AUTO lets OpenVINO pick the best available device automatically.
    OPENVINO_DEVICE: str = "AUTO"

    # Model precision indicator (for logging/metrics only; actual precision
    # is determined by the converted model file).
    OPENVINO_PRECISION: str = "INT8"

    # Detection confidence threshold (0.0 - 1.0).
    YOLO_CONFIDENCE: float = 0.35

    # Non-Maximum Suppression IoU threshold (0.0 - 1.0).
    # Only used for legacy YOLOv8 models; YOLO26 is end-to-end (no NMS).
    YOLO_NMS_IOU: float = 0.5

    # Strict list of classes to keep after inference.
    ALLOWED_CLASSES: list[str] = list(DEFAULT_ALLOWED_CLASSES)

    # Optional per-class confidence thresholds. Values override YOLO_CONFIDENCE.
    # YOLO26 e2e models output lower confidence scores than YOLOv8.
    YOLO_CLASS_CONFIDENCE_THRESHOLDS: dict[str, float] = {
        "person": 0.35,
        "bicycle": 0.35,
        "car": 0.35,
        "motorcycle": 0.35,
        "bus": 0.35,
        "truck": 0.35,
    }

    # Debug logging for postprocess raw detections before filtering.
    YOLO_LOG_RAW_DETECTIONS: bool = False
    YOLO_RAW_DETECTIONS_MAX_LOG: int = 20

    # -- ROI / Intrusion Settings --------------------------------------------

    # Enable ROI-based intrusion evaluation in post-inference pipeline.
    ROI_ENABLED: bool = True

    # Path to ROI zones JSON (relative to backend/).
    ROI_ZONES_CONFIG_PATH: str = "yolo_classifier/intrusion_monitor/zones_config.json"

    # Legacy ROI coordinate reference size (used when config points are pixels).
    ROI_REFERENCE_WIDTH: int = 960
    ROI_REFERENCE_HEIGHT: int = 544

    # Default dwell threshold for ROI violations.
    ROI_DEFAULT_DWELL_SEC: float = 5.0

    # Cooldown for repeated alerts on the same tracked object in the same zone.
    ROI_ALERT_COOLDOWN_SEC: float = 10.0

    # Classes that are considered intruders when entering ROI.
    ROI_INTRUDER_CLASSES: list[str] = ["person"]

    # Classes to include in JSONL activity logging.
    MONITORED_CLASSES: list[str] = list(DEFAULT_ALLOWED_CLASSES)

    # JSON-lines output path for tracked ROI/detection events.
    ROI_EVENTS_LOG_PATH: str = "yolo_classifier/intrusion_monitor/roi_events.jsonl"
    # Optional dual-write: persist per-frame ROI events into DB table `roi_events`.
    ROI_EVENTS_WRITE_DB: bool = False

    # -- Tracker Settings -----------------------------------------------------

    TRACKER_MAX_AGE: int = 30
    TRACKER_N_INIT: int = 3

    # -- Stream Processing Settings -------------------------------------------

    MAX_STREAMS: int = 20
    FRAME_SKIP: int = 6

    # -- Batching & Worker Pool -----------------------------------------------

    # Enable micro-batching of frames across camera streams.
    BATCH_ENABLED: bool = True

    # Maximum frames per inference batch.
    BATCH_MAX_SIZE: int = 8

    # Micro-batch collection window (milliseconds).
    BATCH_TIMEOUT_MS: int = 20

    # Number of inference threads in the worker pool.
    # OpenVINO releases the GIL, so threads provide true parallelism.
    INFERENCE_WORKERS: int = 2

    # Max pending frames in inference queue (backpressure limit).
    INFERENCE_QUEUE_MAX: int = 64

    # -- Adaptive FPS ---------------------------------------------------------

    # Dynamically adjust frame_skip based on inference latency.
    ADAPTIVE_FPS_ENABLED: bool = True

    # Target end-to-end latency (ms). If exceeded, frame_skip increases.
    TARGET_LATENCY_MS: int = 100

    # -- CORS -----------------------------------------------------------------

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://frontend:3001",
    ]

    # -- MediaMTX Integration -------------------------------------------------
    # Enable MediaMTX path registration for stream start/stop lifecycle.
    MEDIAMTX_ENABLED: bool = True
    # Base URL for MediaMTX control API.
    MEDIAMTX_API_BASE_URL: str = "http://mediamtx:9997"
    MEDIAMTX_REQUEST_TIMEOUT_SECONDS: float = 3.0
    # Optional basic-auth credentials for MediaMTX control API.
    MEDIAMTX_API_USERNAME: str | None = None
    MEDIAMTX_API_PASSWORD: str | None = None

    # -- Roboflow Secondary Classifier ----------------------------------------

    # Enable Roboflow API as a secondary classifier for enriched detections.
    ROBOFLOW_ENABLED: bool = False

    # API key from https://roboflow.com → Settings → API Keys.
    ROBOFLOW_API_KEY: str = ""

    # Roboflow project model ID (e.g. "weapon-detection-abc12").
    ROBOFLOW_MODEL_ID: str = ""

    # Model version number.
    ROBOFLOW_MODEL_VERSION: int = 1

    # Minimum confidence for Roboflow predictions (0.0 - 1.0).
    ROBOFLOW_CONFIDENCE: float = 0.40

    # Only send crops of these YOLO classes to Roboflow (saves API calls).
    ROBOFLOW_TRIGGER_CLASSES: list[str] = ["person"]

    # Max concurrent Roboflow API requests (rate limiting).
    ROBOFLOW_MAX_CONCURRENT: int = 2

    # Cooldown per tracked object — skip Roboflow if already classified within N seconds.
    ROBOFLOW_COOLDOWN_SEC: float = 10.0

    # Roboflow inference API base URL.
    ROBOFLOW_API_URL: str = "https://detect.roboflow.com"

    # -- ViT Crime Classifier (Local) ----------------------------------------

    # Enable the ViT-based crime classifier as a secondary analysis step.
    CRIME_CLASSIFIER_ENABLED: bool = True

    # HuggingFace model ID for auto-download.
    CRIME_CLASSIFIER_MODEL_ID: str = "Nikeytas/google-vit-best-crime-detector"

    # Minimum confidence to treat a ViT prediction as a crime event.
    CRIME_CLASSIFIER_CONFIDENCE: float = 0.60

    # Only classify crops of these YOLO classes.
    CRIME_CLASSIFIER_TRIGGER_CLASSES: list[str] = ["person"]

    # Cooldown per tracked object — skip classification if already processed within N seconds.
    CRIME_CLASSIFIER_COOLDOWN_SEC: float = 15.0

    # Max concurrent classification tasks.
    CRIME_CLASSIFIER_MAX_CONCURRENT: int = 1

    # Torch device for inference: "cpu" or "cuda".
    CRIME_CLASSIFIER_DEVICE: str = "cpu"

    # Local directory to cache the downloaded model.
    CRIME_CLASSIFIER_CACHE_DIR: str = "models/crime_classifier"

    # -- Data Retention -------------------------------------------------------
    RETENTION_ENABLED: bool = True
    RETENTION_DAYS: int = 30
    RETENTION_RUN_INTERVAL_SECONDS: int = 60 * 60 * 24
    # Keep unresolved/active alerts by default.
    RETENTION_DELETE_RESOLVED_ALERTS_ONLY: bool = True
    # Optional extended cleanup for large deployments.
    RETENTION_DELETE_ROI_EVENTS: bool = False
    RETENTION_DELETE_ANALYTICS_SNAPSHOTS: bool = False

    @field_validator("DEBUG", mode="before")
    @classmethod
    def _coerce_debug(cls, value):  # type: ignore[no-untyped-def]
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on", "debug", "development", "dev"}:
            return True
        if text in {"0", "false", "no", "off", "release", "production", "prod"}:
            return False
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
