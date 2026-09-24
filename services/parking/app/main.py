"""Argus parking service: gate plate OCR, bay occupancy, sessions, ParkBot.

Runs on its own host. Talks to surveillance only through the internal
mTLS API (events) and trusts user tokens issued by surveillance (JWKS).
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.mesh import mesh
from app.models import Camera
from app.routers import cameras, internal, parking, parking_chat
from app.services.auth import WS_AUTH_SUBPROTOCOL, authenticate_websocket
from app.services.outbox import outbox_runner
from app.services.stream_manager import stream_manager
from app.services.websocket_manager import ws_manager
from argus_vision.engine import detector
from argus_vision.inference_worker import InferenceWorkerPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

EVENTS_CHANNEL = "parking"

inference_pool: InferenceWorkerPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global inference_pool
    settings = get_settings()
    logger.info("Starting %s", settings.APP_NAME)

    # Fail fast on a missing service identity (ephemeral only in DEBUG).
    mesh.signer()

    detector.initialize()
    await init_db()

    inference_pool = InferenceWorkerPool(
        detector=detector,
        num_workers=settings.INFERENCE_WORKERS,
        max_batch_size=settings.BATCH_MAX_SIZE,
        batch_timeout_ms=settings.BATCH_TIMEOUT_MS,
        queue_max_size=settings.INFERENCE_QUEUE_MAX,
    )
    await inference_pool.start()
    stream_manager.set_inference_pool(inference_pool)
    outbox_runner.start()

    yield

    await outbox_runner.stop()
    await stream_manager.stop_all()
    await inference_pool.shutdown()
    detector.shutdown()


app = FastAPI(
    title="Argus Parking API",
    description="Gate plate OCR, bay occupancy, parking sessions and ParkBot",
    version="2.0.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(cameras.router, prefix="/api/v1")
app.include_router(parking.router, prefix="/api/v1")
app.include_router(parking_chat.router, prefix="/api/v1")
app.include_router(internal.router)


@app.get("/api/v1/parking/health")
async def health():
    return {"status": "healthy", "active_streams": stream_manager.active_count}


async def _serve_channel(websocket: WebSocket, channel: str) -> None:
    principal = await authenticate_websocket(websocket)
    if principal is None:
        await websocket.close(code=4001)
        return
    if channel != EVENTS_CHANNEL:
        # Camera channels are tenant-scoped: the camera must belong to the caller.
        try:
            async with get_session_factory()() as session:
                camera = (
                    await session.execute(
                        select(Camera).where(Camera.id == channel, Camera.tenant_id == principal.tenant_id)
                    )
                ).scalar_one_or_none()
        except (SQLAlchemyError, RuntimeError):
            logger.exception("WebSocket camera authorization failed")
            camera = None
        if camera is None:
            await websocket.close(code=4001)
            return
    await ws_manager.connect(websocket, channel, tenant_id=principal.tenant_id, subprotocol=WS_AUTH_SUBPROTOCOL)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(websocket, channel, tenant_id=principal.tenant_id)


@app.websocket("/ws/parking")
async def events_socket(websocket: WebSocket):
    await _serve_channel(websocket, EVENTS_CHANNEL)


@app.websocket("/ws/parking/{camera_id}")
async def camera_socket(websocket: WebSocket, camera_id: str):
    await _serve_channel(websocket, camera_id)
