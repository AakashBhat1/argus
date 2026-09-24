"""Stream lifecycle for parking cameras (gate and lot).

One capture per camera. When MediaMTX ingests an RTSP camera for browser
playback, analytics reads the MediaMTX path instead of opening a second
session on the camera.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import cv2
import httpx

from app.config import get_settings
from app.models import Camera
from app.services.parking_occupancy_service import (
    apply_occupancy_tick,
    persist_anomaly_alerts,
    warm_camera_slots,
)
from app.services.pipeline import ParkingFrameResult, ParkingPipeline
from app.services.websocket_manager import ws_manager
from app.utils import utc_now
from argus_common.net import StreamTargetError, is_network_source, policy_from_settings, recheck_stream_target, redact_url
from argus_vision.metrics import inference_metrics
from argus_vision.sources import (
    encode_frame_to_base64 as _encode_frame_to_base64,
    mediamtx_can_pull as _mediamtx_can_pull,
    mediamtx_read_url,
    open_capture as _open_capture,
)

logger = logging.getLogger(__name__)


class ParkingStream:
    def __init__(self, camera: Camera, inference_pool, capture_url: Optional[str] = None):
        self.camera_id = str(camera.id)
        self.camera_name = camera.name
        self.tenant_id = camera.tenant_id
        self.stream_url = camera.stream_url
        self.role = camera.role or "parking"
        self._capture_url = capture_url or camera.stream_url
        self._pipeline = ParkingPipeline(
            camera_id=self.camera_id,
            camera_name=camera.name,
            tenant_id=camera.tenant_id,
            role=self.role,
            gate_roi=camera.gate_roi,
        )
        self._inference_pool = inference_pool
        self._settings = get_settings()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._frame_count = 0
        self._fps = 0.0
        self._start_time = 0.0
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_base_delay = 0.1
        self._uses_mediamtx = bool(
            self._settings.MEDIAMTX_ENABLED and _mediamtx_can_pull(self.stream_url)
        )
        self._frame_skip = max(1, int(self._settings.FRAME_SKIP))
        self._last_track_count = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_video_source(self) -> bool:
        return self.stream_url.strip().startswith("video://")

    @property
    def pipeline(self) -> ParkingPipeline:
        return self._pipeline

    async def start(self) -> bool:
        if self._running:
            return True
        if self.role == "parking":
            try:
                await warm_camera_slots(self.camera_id, self.tenant_id)
            except Exception:
                logger.exception("Failed to warm parking slots for camera %s", self.camera_id)
        loop = asyncio.get_running_loop()
        test_cap = await loop.run_in_executor(None, _open_capture, self._capture_url)
        can_open = test_cap.isOpened()
        test_cap.release()
        if not can_open:
            logger.error(
                "Cannot start parking stream '%s': invalid source %s",
                self.camera_name,
                redact_url(self._capture_url),
            )
            return False
        self._running = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._process_loop())
        logger.info("Parking stream started: %s (%s)", self.camera_name, self.camera_id)
        return True

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._cap is not None and self._cap.isOpened():
            self._cap.release()
        self._pipeline.reset()

    def update_gate(self, role: Optional[str], gate_roi) -> None:
        self.role = (role or self.role).lower()
        self._pipeline.update_gate(self.role, gate_roi)

    async def _read(self, loop):
        return await loop.run_in_executor(None, self._cap.read)

    async def _process_loop(self) -> None:
        loop = asyncio.get_running_loop()
        self._pipeline.bind_loop(loop)
        self._cap = await loop.run_in_executor(None, _open_capture, self._capture_url)
        if not self._cap.isOpened():
            logger.error("Cannot open parking stream: %s", redact_url(self._capture_url))
            self._running = False
            return
        try:
            while self._running:
                ok, frame = await self._read(loop)
                if not ok:
                    if self.is_video_source:
                        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    self._reconnect_attempts += 1
                    if self._reconnect_attempts > self._max_reconnect_attempts:
                        logger.error("Parking stream %s lost; giving up", self.camera_name)
                        self._running = False
                        break
                    backoff = min(self._reconnect_base_delay * (2 ** self._reconnect_attempts), 30)
                    await asyncio.sleep(backoff)
                    self._cap.release()
                    self._cap = await loop.run_in_executor(None, _open_capture, self._capture_url)
                    continue
                self._reconnect_attempts = 0
                self._frame_count += 1
                if self._frame_count % self._frame_skip != 0:
                    continue
                await self._process_frame(loop, frame)
                await asyncio.sleep(0.001)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Parking stream error for %s", self.camera_name)
        finally:
            if self._cap is not None and self._cap.isOpened():
                self._cap.release()

    async def _process_frame(self, loop, frame) -> None:
        started = time.perf_counter()
        detections = await self._inference_pool.submit(frame, self.camera_id)
        infer_ms = (time.perf_counter() - started) * 1000
        inference_metrics.record_inference(camera_id=self.camera_id, latency_ms=infer_ms)

        result: ParkingFrameResult = await loop.run_in_executor(
            None, self._pipeline.process, detections, frame, utc_now()
        )
        self._last_track_count = len(result.tracked_objects)
        elapsed = time.time() - self._start_time
        self._fps = self._frame_count / elapsed if elapsed > 0 else 0.0

        if result.slot_readings:
            asyncio.create_task(
                apply_occupancy_tick(self.camera_id, self.tenant_id, result.slot_transitions)
            )
        if result.anomaly_alerts:
            asyncio.create_task(persist_anomaly_alerts(result.anomaly_alerts))

        payload: dict = {
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "role": self.role,
            "frame_number": self._frame_count,
            "timestamp": utc_now().isoformat() + "Z",
            "detections": result.tracked_objects,
            "parking_slots": result.slot_payload(),
            "fps": round(self._fps, 1),
            "frame_width": int(frame.shape[1]),
            "frame_height": int(frame.shape[0]),
            "inference_ms": round(infer_ms, 1),
            "is_video_source": self.is_video_source,
            "media_transport": "webrtc" if self._uses_mediamtx else "websocket_jpeg",
        }
        if not self._uses_mediamtx:
            payload["frame_image"] = await loop.run_in_executor(
                None, _encode_frame_to_base64, frame, 60
            )
        await ws_manager.broadcast_to_channel(
            self.tenant_id, self.camera_id, {"type": "detections", "data": payload}
        )

    def status(self) -> dict:
        # Same shape as the surveillance stream status the dashboard renders.
        return {
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "service": "parking",
            "role": self.role,
            "is_running": self._running,
            "is_paused": False,
            "is_video_source": self.is_video_source,
            "fps": round(self._fps, 1),
            "frame_count": self._frame_count,
            "active_tracks": self._last_track_count,
            "uptime_seconds": round(time.time() - self._start_time, 1) if self._running else 0,
            "current_frame_skip": self._frame_skip,
            "media_transport": "webrtc" if self._uses_mediamtx else "websocket_jpeg",
            "gate_ocr": self._pipeline.gate_ocr_status(),
        }


class ParkingStreamManager:
    def __init__(self) -> None:
        self._streams: dict[str, ParkingStream] = {}
        self._inference_pool = None

    def set_inference_pool(self, pool) -> None:
        self._inference_pool = pool

    def get_stream(self, camera_id: str) -> Optional[ParkingStream]:
        return self._streams.get(str(camera_id))

    @property
    def active_count(self) -> int:
        return sum(1 for stream in self._streams.values() if stream.is_running)

    def _mediamtx_auth(self):
        settings = get_settings()
        if settings.MEDIAMTX_API_USERNAME and settings.MEDIAMTX_API_PASSWORD:
            return (settings.MEDIAMTX_API_USERNAME, settings.MEDIAMTX_API_PASSWORD)
        return None

    async def start_stream(self, camera: Camera) -> bool:
        settings = get_settings()
        camera_id = str(camera.id)
        existing = self._streams.get(camera_id)
        if existing is not None:
            if existing.is_running:
                return True
            await existing.stop()
            del self._streams[camera_id]
        if self._inference_pool is None:
            logger.error("Cannot start parking stream: inference pool not initialized")
            return False
        if len(self._streams) >= settings.MAX_STREAMS:
            logger.error("Parking stream limit reached (%s)", settings.MAX_STREAMS)
            return False
        if is_network_source(camera.stream_url):
            try:
                recheck_stream_target(camera.stream_url, policy_from_settings(settings))
            except StreamTargetError as exc:
                logger.error("Refusing parking camera %s: %s", camera_id, exc)
                return False

        capture_url: Optional[str] = None
        if settings.MEDIAMTX_ENABLED and _mediamtx_can_pull(camera.stream_url):
            registered = False
            try:
                async with httpx.AsyncClient(timeout=settings.MEDIAMTX_REQUEST_TIMEOUT_SECONDS) as client:
                    response = await client.post(
                        f"{settings.MEDIAMTX_API_BASE_URL.rstrip('/')}/v3/config/paths/add/{camera_id}",
                        auth=self._mediamtx_auth(),
                        json={"source": camera.stream_url},
                    )
                    registered = response.status_code in (200, 400)
            except httpx.HTTPError as exc:
                logger.warning("MediaMTX registration failed for %s: %s", camera_id, exc)
            if registered and settings.MEDIAMTX_SINGLE_INGEST:
                capture_url = mediamtx_read_url(camera_id, settings)

        stream = ParkingStream(camera, self._inference_pool, capture_url=capture_url)
        if not await stream.start():
            return False
        self._streams[camera_id] = stream
        return True

    async def stop_stream(self, camera_id: str) -> None:
        settings = get_settings()
        stream = self._streams.pop(str(camera_id), None)
        if stream is not None:
            await stream.stop()
        if settings.MEDIAMTX_ENABLED:
            try:
                async with httpx.AsyncClient(timeout=settings.MEDIAMTX_REQUEST_TIMEOUT_SECONDS) as client:
                    await client.delete(
                        f"{settings.MEDIAMTX_API_BASE_URL.rstrip('/')}/v3/config/paths/delete/{camera_id}",
                        auth=self._mediamtx_auth(),
                    )
            except httpx.HTTPError:
                pass

    async def stop_all(self) -> None:
        for camera_id in list(self._streams):
            await self.stop_stream(camera_id)

    def update_camera_gate(self, camera_id: str, role: Optional[str], gate_roi) -> bool:
        stream = self._streams.get(str(camera_id))
        if stream is None:
            return False
        stream.update_gate(role, gate_roi)
        return True

    def get_all_status(self, tenant_id: Optional[str] = None) -> list[dict]:
        return [
            stream.status()
            for stream in self._streams.values()
            if tenant_id is None or stream.tenant_id == tenant_id
        ]


stream_manager = ParkingStreamManager()
