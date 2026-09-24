"""Parking service configuration."""

import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import SettingsConfigDict

from argus_common.mesh import ServiceMeshSettings
from argus_vision import settings as vision_settings
from argus_vision.settings import VisionSettings

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "parking.db")


def env_file_path() -> str | None:
    """Dotenv file to load; ``ARGUS_ENV_FILE=""`` disables it (tests)."""
    override = os.environ.get("ARGUS_ENV_FILE")
    if override is not None:
        return override.strip() or None
    return os.path.join(BASE_DIR, ".env")


class Settings(VisionSettings, ServiceMeshSettings):
    """Parking service settings.

    Vision pipeline settings come from ``VisionSettings``; service identity,
    peer keys and mTLS from ``ServiceMeshSettings``.
    """

    model_config = SettingsConfigDict(extra="ignore")

    APP_NAME: str = "Argus Parking"
    DEBUG: bool = False
    SERVICE_DIR: str = BASE_DIR
    SERVICE_NAME: str = "parking"

    DATABASE_URL: str = f"sqlite+aiosqlite:///{DB_PATH}"

    CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
    ]

    # -- User authentication (tokens issued by the surveillance service) -----
    AUTH_ISSUER: str = "argus-surveillance"
    AUTH_AUDIENCE: str = "argus"
    # JWKS of the identity provider, fetched over the internal mTLS channel,
    # e.g. https://surveillance-internal:8443/.well-known/jwks.json
    AUTH_JWKS_URL: str | None = None
    # Alternatively a JWKS file distributed with the deployment.
    AUTH_JWKS_FILE: str | None = None
    AUTH_JWKS_TTL_SECONDS: float = 300.0
    # Must match the identity provider's setting: dashboard session cookies
    # use Secure, prefixed names unless both run in DEBUG over plain http.
    AUTH_COOKIE_SECURE: bool = True

    # 32-byte key sealing camera credentials in the database (deploy/pki.sh).
    # Required unless DEBUG. Retired keys (*.key) stay readable for rotation.
    CAMERA_SECRETS_KEY_FILE: str | None = None
    CAMERA_SECRETS_PREVIOUS_KEYS_DIR: str | None = None

    # -- Peers ---------------------------------------------------------------
    # Internal base URL of the surveillance service (receives parking events).
    SURVEILLANCE_INTERNAL_URL: str | None = None
    OUTBOX_POLL_INTERVAL_SEC: float = 1.0

    # -- Vision parking occupancy --------------------------------------------
    PARKING_OCCUPANCY_ENABLED: bool = True
    PARKING_OCCUPANCY_INTERVAL_SEC: float = 2.0
    PARKING_OCCUPANCY_HI: float = 0.22
    PARKING_OCCUPANCY_LO: float = 0.10
    PARKING_OCCUPANCY_IOU_MIN: float = 0.40
    PARKING_OCCUPANCY_TEXTURE_FALLBACK: bool = False
    # Detector classes that occupy a bay (trucks/buses are scored by how much
    # of the bay they cover; motorcycles by how much of them is inside it).
    PARKING_VEHICLE_CLASSES: list[str] = ["car", "truck", "bus", "motorcycle"]
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

    # Vehicle profile types whose plate read authorizes arrivals.
    PARKING_AUTHORIZED_PROFILE_TYPES: list[str] = ["vip", "resident", "staff"]

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
    return Settings(_env_file=env_file_path())


vision_settings.configure(get_settings)
