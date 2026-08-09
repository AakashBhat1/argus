"""
Smart Parking System — Configuration
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Database
DATABASE_PATH = os.environ.get(
    "DATABASE_PATH", os.path.join(BASE_DIR, "parking_system.db")
)

# Ollama AI
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:0.6b")

# Detection
CASCADE_PATH = os.path.join(BASE_DIR, "assets", "indian_license_plate.xml")
MIN_PLATE_AREA = 500

# Video / Camera
CARPARK_VIDEO_PATH = os.environ.get("CARPARK_VIDEO", "")
CARPARK_POS_PATH = os.environ.get("CARPARK_POS", "")

# Parking
DEFAULT_SPACES = 24  # Default simulated spaces if no video/pos file
PARKING_RATE_PER_HOUR = 20  # INR

# Flask
SECRET_KEY = os.environ.get("SECRET_KEY", "smart-parking-secret-2026")
DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"
HOST = "0.0.0.0"
PORT = 5000

# Optional admin token (demo/off-by-default).
# When set (via env ADMIN_TOKEN=...), the gated admin endpoints (config, session/reset, admin/connections)
# require X-Admin-Token: <value> (or Authorization: Bearer <value>) header.
# If unset/empty (the default), those endpoints remain open exactly as before (for local dev + current SvelteKit admin panel).
# NOTE: shipping a static token to a browser SPA is demo-grade gating only. Real auth requires a server-side
# login/session flow (future work). See CONTRACT for endpoint notes.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN") or None
