import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.detection import detector
from app.routers import alerts, analytics, cameras, crime, detections, intents, metrics, roboflow, streams, videos, zones, auth
from app.services.inference_worker import InferenceWorkerPool
from app.services.metrics import inference_metrics
from app.services.roboflow_classifier import roboflow_classifier
from app.services.crime_classifier import crime_classifier
from app.services.stream_manager import stream_manager
from app.services.websocket_manager import ws_manager
from app.services.retention import retention_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inference Worker Pool (global, started in lifespan)
# ---------------------------------------------------------------------------
inference_pool: InferenceWorkerPool = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global inference_pool

    settings = get_settings()
    logger.info(f"Starting {settings.APP_NAME}")

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
    model_info = detector.get_model_info()
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

    yield

    # Shutdown
    logger.info("Shutting down, stopping all streams...")
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
    # Authenticate via ?token= query parameter
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001)
        return
    try:
        from jose import jwt as _jwt, JWTError as _JWTError
        from app.services.auth import SECRET_KEY, ALGORITHM
        payload = _jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            await websocket.close(code=4001)
            return

        from app.database import get_session_factory
        from app.models import User, Camera
        from sqlalchemy import select

        session_factory = get_session_factory()
        async with session_factory() as session:
            # Check user is active in DB
            db_res = await session.execute(select(User).where(User.username == username))
            db_user = db_res.scalar_one_or_none()
            if not db_user or not db_user.is_active:
                await websocket.close(code=4001)
                return
            tenant_id = db_user.tenant_id

            # Verify camera ownership if subscribing to camera channel
            if channel not in ("global", "alerts"):
                cam_res = await session.execute(
                    select(Camera).where(Camera.id == channel, Camera.tenant_id == tenant_id)
                )
                camera = cam_res.scalar_one_or_none()
                if not camera:
                    await websocket.close(code=4001)
                    return
    except Exception:
        await websocket.close(code=4001)
        return

    await ws_manager.connect(websocket, channel, tenant_id=tenant_id)
    try:
        while True:
            data = await websocket.receive_text()
            logger.debug(f"WS message on {channel}: {data}")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket, channel, tenant_id=tenant_id)
    except Exception:
        await ws_manager.disconnect(websocket, channel, tenant_id=tenant_id)
