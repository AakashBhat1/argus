"""
Smart Parking System — Application Entry Point
Flask app factory with blueprint registration.
"""
import os
import atexit
from flask import Flask
import config
from models import database as db
from services import camera_service


def create_app():
    """Flask application factory."""
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    # Ensure directories exist
    # Note: static/images is kept (runtime plate crops written by camera_service.py).
    # Legacy dirs (static/css, static/js, templates/components) retired in R17.
    os.makedirs("static/images", exist_ok=True)

    # Initialize database
    db.init_db()
    print("[App] Database initialized")

    # Register blueprints (headless API only — legacy main_bp retired)
    from routes.api_routes import api_bp
    from routes.feed_routes import feed_bp
    from routes.chat_routes import chat_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(feed_bp)
    app.register_blueprint(chat_bp)

    print("[App] Blueprints registered: api, feed, chat")
    print(f"[App] Ollama model: {config.OLLAMA_MODEL}")
    print(f"[App] Database: {config.DATABASE_PATH}")

    return app


# Cleanup on shutdown
def _cleanup():
    print("[App] Shutting down...")
    camera_service.stop()
    db.close()


atexit.register(_cleanup)


if __name__ == "__main__":
    app = create_app()
    print(f"\n{'='*50}")
    print(f"  Smart Parking System v2.0")
    print(f"  http://localhost:{config.PORT}")
    print(f"{'='*50}\n")
    app.run(
        debug=config.DEBUG,
        host=config.HOST,
        port=config.PORT,
        threaded=True,
    )
