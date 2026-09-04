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

    # OpenVINO PERFORMANCE_HINT: LATENCY | THROUGHPUT | (empty = plugin default).
    # The detector issues synchronous single-frame requests, so LATENCY is the
    # right choice everywhere. THROUGHPUT on Intel iGPUs allocates ~16 streams
    # and makes each blocking call hundreds of times slower (measured 1.6 s vs
    # 4.5 ms for YOLOv8n on an Arc iGPU).
    OPENVINO_PERFORMANCE_HINT: str = "LATENCY"

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

    # Reference point used for zone membership: "foot" (bottom-centre of the
    # bbox, correct for ground-plane zones) or "center".
    ROI_ANCHOR: str = "foot"

    # Dwell state survives tracker dropouts shorter than this (seconds).
    ROI_TRACK_GRACE_SEC: float = 2.0

    # A zone incident stays open (suppressing duplicate alerts) until no
    # intruder has been seen in that zone for this many seconds.
    ROI_INCIDENT_HOLDDOWN_SEC: float = 30.0

    # -- Contextual Risk Engine ----------------------------------------------

    RISK_ENGINE_ENABLED: bool = True
    # Score thresholds (0-100) for the escalation ladder.
    RISK_LEVEL_SUSPICIOUS: int = 25
    RISK_LEVEL_ALERT: int = 50
    RISK_LEVEL_CRITICAL: int = 75
    # Minimum seconds between escalation alerts for the same track.
    RISK_ALERT_COOLDOWN_SEC: float = 20.0
    # Quiet hours add risk (local time in RISK_QUIET_HOURS_TZ).
    RISK_QUIET_HOURS_START: int = 22
    RISK_QUIET_HOURS_END: int = 6
    RISK_QUIET_HOURS_TZ: str = "UTC"
    # Persons within this ground distance for RISK_CLOSE_CONTACT_SEC count as
    # "close contact" (pre-fight signal, also gates the heavier classifiers).
    RISK_CLOSE_CONTACT_M: float = 1.5
    RISK_CLOSE_CONTACT_SEC: float = 2.0
    # A person that appears next to a vehicle inherits that vehicle's
    # authorization for this long (seconds).
    RISK_VEHICLE_LINK_TTL_SEC: float = 600.0
    # Vehicle profile types treated as authorized when a plate is read.
    RISK_AUTHORIZED_PROFILE_TYPES: list[str] = ["vip", "resident", "staff"]
    # Default duration for a manually granted visitor window (minutes).
    RISK_MANUAL_GRANT_MINUTES: int = 15

    # Default camera horizontal field of view when no calibration is stored.
    CAMERA_DEFAULT_HFOV_DEG: float = 84.0

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

    # Allow camera stream URLs that point at private/loopback/link-local IPs
    # and plain http(s) sources (e.g. DroidCam / IP Webcam on the LAN).
    # Keep False in production: SSRF protection rejects such URLs.
    ALLOW_PRIVATE_STREAM_URLS: bool = False

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

    CRIME_CLASSIFIER_TRIGGER_ON_PARKING: bool = True

    # -- Vision parking occupancy --------------------------------------------
    PARKING_OCCUPANCY_ENABLED: bool = True
    PARKING_OCCUPANCY_INTERVAL_SEC: float = 2.0
    PARKING_OCCUPANCY_HI: float = 0.22
    PARKING_OCCUPANCY_LO: float = 0.10
    PARKING_OCCUPANCY_IOU_MIN: float = 0.40
    PARKING_OCCUPANCY_DEBOUNCE_FRAMES: int = 5

    # -- Parking anomaly rules ------------------------------------------------
    PARKING_ANOMALY_ENABLED: bool = True
    PARKING_ANOMALY_COOLDOWN_SEC: float = 300.0
    PARKING_GHOST_OCCUPANCY_MIN: float = 10.0
    PARKING_GHOST_PLATE_LOOKBACK_MIN: float = 15.0
    PARKING_LOITER_MIN_SEC: float = 45.0
    PARKING_CAR_HOP_MIN_SLOTS: int = 3
    PARKING_CAR_HOP_MIN_STATIONARY: float = 0.35
    PARKING_CHURN_THRESHOLD: int = 6
    PARKING_CHURN_WINDOW_MIN: float = 15.0
    PARKING_QUIET_HOURS_START: int = 22
    PARKING_QUIET_HOURS_END: int = 6
    PARKING_QUIET_HOURS_TZ: str = 'UTC'

    # -- Data Retention -------------------------------------------------------
    RETENTION_ENABLED: bool = True
    RETENTION_DAYS: int = 30
    RETENTION_RUN_INTERVAL_SECONDS: int = 60 * 60 * 24
    # Keep unresolved/active alerts by default.
    RETENTION_DELETE_RESOLVED_ALERTS_ONLY: bool = True
    # Optional extended cleanup for large deployments.
    RETENTION_DELETE_ROI_EVENTS: bool = False
    RETENTION_DELETE_ANALYTICS_SNAPSHOTS: bool = False

    # -- Smart Parking --------------------------------------------------------
    # Hourly tariff in INR (rounded up per hour after free-window).
    PARKING_RATE_PER_HOUR: float = 20.0
    # Stays under this many minutes bill a flat short-stay rate.
    PARKING_FREE_MINUTES: int = 5
    PARKING_SHORT_STAY_RATE: float = 10.0
    # Minimum OCR confidence to accept a plate reading.
    PARKING_OCR_CONFIDENCE_THRESHOLD: float = 0.50
    # Vehicle classes that can trigger gate OCR.
    PARKING_OCR_TRIGGER_CLASSES: list[str] = ["car", "motorcycle", "bus", "truck"]
    # ParkBot / Ollama
    PARKING_OLLAMA_BASE_URL: str = "http://localhost:11434"
    PARKING_OLLAMA_MODEL: str = "qwen3:0.6b"
    PARKING_OLLAMA_TIMEOUT_SECONDS: float = 30.0

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
