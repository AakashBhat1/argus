import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.detection import detector
from app.models import Camera
from app.routers import alerts, analytics, cameras, crime, detections, intents, internal, metrics, roboflow, security, streams, videos, zones, auth
from app.services.auth import authenticate_websocket, jwks_document, signing_key
from argus_common.web_auth import WS_AUTH_CLOSE_CODE, hold_until_expiry
from argus_vision.inference_worker import InferenceWorkerPool
from argus_vision.metrics import inference_metrics
from app.services.roboflow_classifier import roboflow_classifier
from app.services.crime_classifier import crime_classifier
from app.services.stream_manager import stream_manager
from app.services.websocket_manager import ws_manager
from app.services.outbox import outbox_runner
from app.services.retention import retention_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

NON_CAMERA_CHANNELS = frozenset({"global", "alerts"})

# ---------------------------------------------------------------------------
# Inference Worker Pool (global, started in lifespan)
# ---------------------------------------------------------------------------
inference_pool: InferenceWorkerPool = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global inference_pool

    settings = get_settings()
    logger.info(f"Starting {settings.APP_NAME}")

    # Fail fast on missing token-signing keys (ephemeral only in DEBUG).
    signing_key()

    # Load and validate model weights during application startup, not import.
    model_info = detector.initialize().get_model_info()

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Initialize the inference worker pool
    inference_pool = InferenceWorkerPool(
        detector=detector,
        num_workers=settings.INFERENCE_WORKERS,
        max_batch_size=settings.BATCH_MAX_SIZE,
        batch_timeout_ms=settings.BATCH_TIMEOUT_MS,
        queue_max_size=settings.INFERENCE_QUEUE_MAX,
    )
    await inference_pool.start()

    # Set device info on metrics singleton
    inference_metrics.set_device_info(
        device=model_info["device_actual"],
        model_path=model_info["model"],
    )

    # Inject the worker pool into the stream manager
    stream_manager.set_inference_pool(inference_pool)

    logger.info(
        f"OpenVINO inference pipeline ready: "
        f"device={model_info['device_actual']}, "
        f"workers={settings.INFERENCE_WORKERS}, "
        f"batch_size={settings.BATCH_MAX_SIZE}"
    )

    # Start data retention worker
    retention_task = asyncio.create_task(retention_worker())

    # Deliver queued events to peer services (parking).
    outbox_runner.start()

    yield

    # Shutdown
    logger.info("Shutting down, stopping all streams...")
    await outbox_runner.stop()
    retention_task.cancel()
    await stream_manager.stop_all()
    await inference_pool.shutdown()
    detector.shutdown()
    logger.info("Shutdown complete")


app = FastAPI(
    title="AI Surveillance System API",
    description="Distributed Real-Time Multi-Camera AI Surveillance and Analytics",
    version="2.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cameras.router, prefix="/api/v1")
app.include_router(detections.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")
app.include_router(analytics.router, prefix="/api/v1")
app.include_router(streams.router, prefix="/api/v1")
app.include_router(metrics.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(roboflow.router, prefix="/api/v1")
app.include_router(crime.router, prefix="/api/v1")
app.include_router(videos.router, prefix="/api/v1")
app.include_router(zones.router, prefix="/api/v1")
app.include_router(intents.router, prefix="/api/v1")
app.include_router(security.router, prefix="/api/v1")
app.include_router(internal.router)


@app.get("/.well-known/jwks.json", include_in_schema=False)
async def jwks_endpoint():
    """Public keys other services use to verify access tokens."""
    return JSONResponse(jwks_document(), headers={"Cache-Control": "public, max-age=300"})


@app.get("/api/v1/health")
async def health_check():
    pool_metrics = inference_pool.get_metrics() if inference_pool else {}
    return {
        "status": "healthy",
        "active_streams": stream_manager.active_count,
        "ws_connections": ws_manager.connection_count,
        "inference_device": detector.get_model_info().get("device_actual", "unknown"),
        "inference_pool": {
            "running": pool_metrics.get("is_running", False),
            "queue_depth": pool_metrics.get("queue_depth", 0),
            "frames_processed": pool_metrics.get("frames_processed", 0),
        },
        "roboflow": {
            "enabled": roboflow_classifier.enabled,
            "requests": roboflow_classifier.get_status().get("total_requests", 0),
        },
        "crime_classifier": {
            "enabled": crime_classifier.enabled,
            "model_loaded": crime_classifier.get_status().get("model_loaded", False),
            "classifications": crime_classifier.get_status().get("total_classifications", 0),
            "crimes_detected": crime_classifier.get_status().get("total_crimes_detected", 0),
        },
    }


@app.websocket("/ws/{channel}")
async def websocket_endpoint(websocket: WebSocket, channel: str):
    auth = await authenticate_websocket(websocket)
    if auth is None:
        await websocket.close(code=WS_AUTH_CLOSE_CODE)
        return

    tenant_id = auth.user.tenant_id
    if channel not in NON_CAMERA_CHANNELS:
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                cam_res = await session.execute(
                    select(Camera).where(Camera.id == channel, Camera.tenant_id == tenant_id)
                )
                camera = cam_res.scalar_one_or_none()
                if not camera:
                    await websocket.close(code=WS_AUTH_CLOSE_CODE)
                    return
        except (SQLAlchemyError, RuntimeError):
            logger.exception("WebSocket camera authorization failed")
            await websocket.close(code=WS_AUTH_CLOSE_CODE)
            return

    await ws_manager.connect(
        websocket,
        channel,
        tenant_id=tenant_id,
        subprotocol=auth.subprotocol,
    )
    try:
        await hold_until_expiry(websocket, auth.expires_at)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("WebSocket on %s ended with an error", channel, exc_info=True)
    finally:
        await ws_manager.disconnect(websocket, channel, tenant_id=tenant_id)
