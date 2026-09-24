import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import SettingsConfigDict

from argus_common.mesh import ServiceMeshSettings
from argus_vision import settings as vision_settings
from argus_vision.settings import DEFAULT_ALLOWED_CLASSES, VisionSettings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "surveillance.db")

def env_file_path() -> str | None:
    """Return the dotenv file to load, or ``None`` to load none.

    ``ARGUS_ENV_FILE`` overrides the location; an empty value disables dotenv
    loading entirely (the test suite does this so a developer's local ``.env``
    cannot change test outcomes). The default is anchored to this service
    directory instead of the process working directory.
    """
    override = os.environ.get("ARGUS_ENV_FILE")
    if override is not None:
        return override.strip() or None
    return os.path.join(BASE_DIR, ".env")


class Settings(VisionSettings, ServiceMeshSettings):
    """Surveillance service configuration.

    Vision pipeline settings (OpenVINO, ROI, tracker, stream sources,
    MediaMTX) are inherited from ``argus_vision.settings.VisionSettings``.
    Unknown environment variables are ignored.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
    )

    SERVICE_DIR: str = BASE_DIR
    SERVICE_NAME: str = "surveillance"

    # Internal base URL of the parking service (receives arming changes).
    # Leave unset when parking is not deployed.
    PARKING_INTERNAL_URL: str | None = None
    OUTBOX_POLL_INTERVAL_SEC: float = 1.0

    APP_NAME: str = "AI Surveillance System"
    DEBUG: bool = False

    # -- Identity provider (access tokens) -----------------------------------
    # Ed25519 private key (PEM, chmod 600) that signs user access tokens.
    # Required unless DEBUG (which falls back to an ephemeral key).
    AUTH_SIGNING_KEY_FILE: str | None = None
    # Public PEMs of retired signing keys still accepted during rotation.
    AUTH_PREVIOUS_PUBLIC_KEYS_DIR: str | None = None
    AUTH_ISSUER: str = "argus-surveillance"
    AUTH_AUDIENCE: str = "argus"

    # Dashboard sessions: httpOnly cookies holding a short access token and a
    # rotating refresh token (see argus_common.web_auth).
    AUTH_SESSION_ACCESS_TTL_SECONDS: int = 600
    # A session ends after this long without a refresh...
    AUTH_SESSION_IDLE_SECONDS: int = 12 * 3600
    # ...and in any case this long after sign-in.
    AUTH_SESSION_MAX_SECONDS: int = 24 * 3600
    # Secure, prefixed cookie names. Plain-http development on a LAN address
    # needs False, which is refused unless DEBUG.
    AUTH_COOKIE_SECURE: bool = True

    # Local SQLite DB for the classifier-only service
    DATABASE_URL: str = f"sqlite+aiosqlite:///{DB_PATH}"

    # Optional extra URLs for compatibility with larger stack configs.
    # These exist in your environment/.env for the full system; we
    # declare them here so Pydantic doesn't raise "extra fields" errors.
    DATABASE_URL_SYNC: str | None = None
    REDIS_URL: str | None = None

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

    # Classes to include in JSONL activity logging.
    MONITORED_CLASSES: list[str] = list(DEFAULT_ALLOWED_CLASSES)

    # JSON-lines output path for tracked ROI/detection events.
    # Relative to SERVICE_DIR.
    ROI_EVENTS_LOG_PATH: str = "intrusion_monitor/roi_events.jsonl"
    # Optional dual-write: persist per-frame ROI events into DB table `roi_events`.
    ROI_EVENTS_WRITE_DB: bool = False

    # -- Stream processing (shared stream/batch settings: VisionSettings) -----

    DETECTION_PERSIST_INTERVAL_SEC: float = 1.0

    # -- CORS -----------------------------------------------------------------

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://frontend:3001",
    ]

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
    # A single still crop cannot establish criminal behaviour. This legacy
    # appearance classifier is experimental and therefore opt-in.
    CRIME_CLASSIFIER_ENABLED: bool = False

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

    # By default the classifier only nudges the risk score. Set true to also
    # raise a MEDIUM "verify footage" alert on its own.
    CRIME_CLASSIFIER_STANDALONE_ALERTS: bool = False

    # Pin the Hugging Face download to an immutable commit and verify the
    # weights file, so an upstream change cannot silently swap the model.
    CRIME_CLASSIFIER_MODEL_REVISION: str | None = None
    CRIME_CLASSIFIER_MODEL_SHA256: str | None = None

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
    return Settings(_env_file=env_file_path())


# Vision modules read this service's settings (and test overrides).
vision_settings.configure(get_settings)
