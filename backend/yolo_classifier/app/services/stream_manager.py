import asyncio
import base64
import logging
import platform
import time
from typing import Optional

import cv2
import numpy as np

from app.config import get_settings
from app import database
from app.detection.roi import IntrusionEvent
from app.models import Alert, AlertSeverity, Camera, Detection
from app.services.intrusion_pipeline import IntrusionPipeline, PipelineResult
from app.services.metrics import inference_metrics
from app.services.crime_classifier import crime_classifier
from app.services.roboflow_classifier import roboflow_classifier
from app.services.websocket_manager import ws_manager
from app.utils import utc_now

logger = logging.getLogger(__name__)


def _resolve_stream_source(stream_url: str):
    """Convert numeric camera strings (e.g. '0') to OpenCV device indexes,
    and video:// URIs to absolute file paths."""
    from pathlib import Path

    value = stream_url.strip()
    if value.isdigit():
        return int(value)
    if value.startswith("video://"):
        filename = value[len("video://"):]
        video_dir = Path(__file__).resolve().parents[2] / "video"
        return str(video_dir / filename)
    return value


def _open_capture(stream_url: str) -> cv2.VideoCapture:
    """Open a capture source for URLs, file paths, or local webcam indexes."""
    source = _resolve_stream_source(stream_url)
    if isinstance(source, int) and platform.system().lower() == "windows":
        return cv2.VideoCapture(source, cv2.CAP_DSHOW)
    cap = cv2.VideoCapture(source)
    # For HTTP/RTSP streams, set buffer and timeout to prevent drops
    if isinstance(source, str) and (source.startswith("http") or source.startswith("rtsp")):
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 10000)
    return cap


def _encode_frame_to_base64(frame: np.ndarray, quality: int = 70) -> str:
    """Encode a frame to base64 JPEG for websocket transport."""
    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not success:
        return ""
    return base64.b64encode(buffer.tobytes()).decode("ascii")


class VideoStream:
    """Manages a single camera video stream with detection and tracking."""

    def __init__(self, camera: Camera, inference_pool):
        self.camera_id = camera.id
        self.stream_url = camera.stream_url
        self.camera_name = camera.name
        self._pipeline = IntrusionPipeline(camera_id=str(camera.id), camera_name=camera.name)
        self._inference_pool = inference_pool

        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None
        self._cap: Optional[cv2.VideoCapture] = None
        self._frame_count = 0
        self._fps = 0.0
        self._start_time = 0.0
        self._last_detections: list[dict] = []
        self._settings = get_settings()
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 5
        self._reconnect_base_delay = 0.1  # seconds; exponential backoff base

        # Adaptive FPS state
        self._current_frame_skip = self._settings.FRAME_SKIP
        self._last_fps_eval_time = time.time()

    async def start(self) -> bool:
        """
        Start the stream after validating that the source can be opened.

        Returns:
            True if stream task started, False if source is invalid/unavailable.
        """
        if self._running:
            return True

        # Preflight validation prevents false-positive "started" responses.
        test_cap = _open_capture(self.stream_url)
        can_open = test_cap.isOpened()
        if test_cap:
            test_cap.release()

        if not can_open:
            logger.error(
                "Cannot start stream '%s': invalid source %s",
                self.camera_name,
                self.stream_url,
            )
            return False

        self._running = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._process_loop())
        logger.info("Stream started: %s (%s)", self.camera_name, self.camera_id)
        return True

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._cap and self._cap.isOpened():
            self._cap.release()
        self._pipeline.reset()
        logger.info("Stream stopped: %s (%s)", self.camera_name, self.camera_id)

    def pause(self):
        self._paused = True
        # Release capture to free resources; save frame position for video files
        self._paused_frame_pos = None
        if self._cap and self._cap.isOpened():
            if self.is_video_source:
                self._paused_frame_pos = int(self._cap.get(cv2.CAP_PROP_POS_FRAMES))
            self._cap.release()
        logger.info("Stream paused: %s (%s)", self.camera_name, self.camera_id)

    def resume(self):
        self._paused = False
        # Reopen capture and seek to saved position
        self._cap = _open_capture(self.stream_url)
        if self._cap and self.is_video_source and hasattr(self, "_paused_frame_pos") and self._paused_frame_pos is not None:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, self._paused_frame_pos)
        logger.info("Stream resumed: %s (%s)", self.camera_name, self.camera_id)

    @property
    def is_video_source(self) -> bool:
        return self.stream_url.strip().startswith("video://")

    def _update_adaptive_fps(self):
        """Dynamically adjust frame skipping to meet latency targets."""
        if not self._settings.ADAPTIVE_FPS_ENABLED or not self._inference_pool:
            return

        now = time.time()
        if now - self._last_fps_eval_time < 5.0:
            return

        metrics = self._inference_pool.get_metrics()
        latency = metrics.get("avg_batch_latency_ms", 0)
        target = self._settings.TARGET_LATENCY_MS

        old_skip = self._current_frame_skip

        if latency > target * 1.5:
            self._current_frame_skip = min(self._current_frame_skip + 1, 6)
        elif latency < target * 0.5:
            self._current_frame_skip = max(self._current_frame_skip - 1, 1)

        if old_skip != self._current_frame_skip:
            logger.info(
                "Adaptive FPS [%s]: latency %.0fms. frame_skip %s -> %s",
                self.camera_name,
                latency,
                old_skip,
                self._current_frame_skip,
            )

        self._last_fps_eval_time = now

    async def _process_loop(self):
        loop = asyncio.get_event_loop()
        self._cap = _open_capture(self.stream_url)

        if not self._cap.isOpened():
            logger.error("Cannot open stream: %s", self.stream_url)
            self._running = False
            return

        try:
            while self._running:
                # When paused, capture is released — sleep until resumed
                if self._paused:
                    await asyncio.sleep(0.2)
                    continue

                # After resume, capture may need to be verified
                if not self._cap or not self._cap.isOpened():
                    self._cap = _open_capture(self.stream_url)
                    if not self._cap.isOpened():
                        await asyncio.sleep(0.5)
                        continue

                ret, frame = await loop.run_in_executor(None, self._cap.read)
                if not ret:
                    # For video files, end-of-file means loop back to start
                    if self.is_video_source:
                        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        logger.info("Video looped: %s", self.camera_name)
                        continue

                    self._reconnect_attempts += 1
                    if self._reconnect_attempts > self._max_reconnect_attempts:
                        logger.error(
                            "Max reconnect attempts (%d) exhausted for %s. Stopping stream.",
                            self._max_reconnect_attempts,
                            self.camera_name,
                        )
                        self._running = False
                        break
                    backoff = min(self._reconnect_base_delay * (2 ** self._reconnect_attempts), 30)
                    logger.warning(
                        "Frame read failed for %s, reconnect attempt %d/%d (backoff %.1fs)...",
                        self.camera_name,
                        self._reconnect_attempts,
                        self._max_reconnect_attempts,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    self._cap.release()
                    self._cap = _open_capture(self.stream_url)
                    continue

                self._reconnect_attempts = 0
                self._frame_count += 1

                self._update_adaptive_fps()

                if self._frame_count % self._current_frame_skip != 0:
                    continue

                t_infer_start = time.perf_counter()
                detections = await self._inference_pool.submit(frame, str(self.camera_id))
                infer_ms = (time.perf_counter() - t_infer_start) * 1000

                pipeline_result: PipelineResult = await loop.run_in_executor(
                    None,
                    self._pipeline.process,
                    detections,
                    frame,
                    utc_now(),
                )
                tracked = pipeline_result.tracked_objects
                intrusion_events = pipeline_result.intrusion_events
                intrusion_payload = pipeline_result.intrusion_payload()

                self._last_detections = tracked
                elapsed = time.time() - self._start_time
                self._fps = self._frame_count / elapsed if elapsed > 0 else 0

                inference_metrics.record_inference(camera_id=str(self.camera_id), latency_ms=infer_ms)

                if tracked:
                    await self._store_detections(tracked)

                # Fire Roboflow secondary classification (non-blocking background task)
                if tracked and roboflow_classifier.enabled:
                    asyncio.create_task(
                        self._roboflow_enrich(frame.copy(), tracked, intrusion_events)
                    )

                # Fire ViT crime classification (non-blocking, only on intrusion)
                crime_results_payload: list[dict] = []
                if intrusion_events and crime_classifier.enabled:
                    asyncio.create_task(
                        self._crime_classify(frame.copy(), tracked, intrusion_events)
                    )

                if tracked or intrusion_events:
                    await self._check_alerts(tracked, intrusion_events)

                payload: dict = {
                    "camera_id": str(self.camera_id),
                    "camera_name": self.camera_name,
                    "frame_number": self._frame_count,
                    "timestamp": utc_now().isoformat() + "Z",
                    "detections": tracked,
                    "intrusions": intrusion_payload,
                    "crime_classifications": crime_results_payload,
                    "fps": round(self._fps, 1),
                    "frame_width": int(frame.shape[1]),
                    "frame_height": int(frame.shape[0]),
                    "frame_skip": self._current_frame_skip,
                    "inference_ms": round(infer_ms, 1),
                    "is_video_source": self.stream_url.strip().startswith("video://"),
                    "is_paused": self._paused,
                    "frame_image": _encode_frame_to_base64(frame, quality=60),
                }
                await ws_manager.broadcast_detections(str(self.camera_id), payload)

                await asyncio.sleep(0.001)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Stream error for %s: %s", self.camera_name, exc)
        finally:
            if self._cap and self._cap.isOpened():
                self._cap.release()

    async def _store_detections(self, tracked_objects: list[dict]):
        try:
            session_factory = database.get_session_factory()
            async with session_factory() as session:
                for obj in tracked_objects:
                    detection = Detection(
                        camera_id=self.camera_id,
                        object_id=obj["object_id"],
                        class_label=obj["class_label"],
                        confidence=obj["confidence"],
                        bbox_x=obj["bbox_x"],
                        bbox_y=obj["bbox_y"],
                        bbox_w=obj["bbox_w"],
                        bbox_h=obj["bbox_h"],
                        frame_number=obj["frame_number"],
                        metadata_={
                            "inside_roi": bool(obj.get("inside_roi", False)),
                            "intrusion": bool(obj.get("intrusion", False)),
                            "roi_zone_ids": obj.get("roi_zone_ids", []),
                            "max_roi_dwell_sec": float(obj.get("max_roi_dwell_sec", 0.0)),
                        },
                    )
                    session.add(detection)
                await session.commit()
        except Exception as exc:
            logger.error("Failed to store detections: %s", exc)

    async def _check_alerts(
        self,
        tracked_objects: list[dict],
        intrusion_events: list[IntrusionEvent],
    ):
        alerts_payload: list[dict] = []
        try:
            session_factory = database.get_session_factory()
            async with session_factory() as session:
                person_count = sum(1 for obj in tracked_objects if obj["class_label"] == "person")
                if person_count > 10:
                    crowd_alert = Alert(
                        camera_id=self.camera_id,
                        type="crowd_detected",
                        severity=AlertSeverity.HIGH.value,
                        trigger_condition=f"person_count > 10 (detected: {person_count})",
                        description=f"High crowd density detected on {self.camera_name}",
                    )
                    session.add(crowd_alert)
                    await session.flush()
                    alerts_payload.append(
                        {
                            "alert_id": str(crowd_alert.id),
                            "camera_id": str(self.camera_id),
                            "type": "crowd_detected",
                            "severity": AlertSeverity.HIGH.value,
                            "person_count": person_count,
                            "timestamp": utc_now().isoformat() + "Z",
                        }
                    )

                for event in intrusion_events:
                    intrusion_alert = Alert(
                        camera_id=self.camera_id,
                        type="intrusion_detected",
                        severity=AlertSeverity.CRITICAL.value,
                        trigger_condition=(
                            f"object_{event.object_id} in zone '{event.zone_name}' for "
                            f"{event.dwell_seconds:.1f}s (threshold {event.threshold_seconds:.1f}s)"
                        ),
                        description=(
                            f"Intruder detected in {event.zone_name} on {self.camera_name} "
                            f"(object {event.object_id})"
                        ),
                        metadata_={
                            "camera_id": event.camera_id,
                            "object_id": event.object_id,
                            "class_label": event.class_label,
                            "zone_id": event.zone_id,
                            "zone_name": event.zone_name,
                            "dwell_seconds": round(event.dwell_seconds, 2),
                            "threshold_seconds": round(event.threshold_seconds, 2),
                            "timestamp_unix": round(event.timestamp_unix, 6),
                        },
                    )
                    session.add(intrusion_alert)
                    await session.flush()
                    alerts_payload.append(
                        {
                            "alert_id": str(intrusion_alert.id),
                            "camera_id": str(self.camera_id),
                            "type": "intrusion_detected",
                            "severity": AlertSeverity.CRITICAL.value,
                            "zone_id": event.zone_id,
                            "zone_name": event.zone_name,
                            "object_id": event.object_id,
                            "dwell_seconds": round(event.dwell_seconds, 2),
                            "threshold_seconds": round(event.threshold_seconds, 2),
                            "timestamp": utc_now().isoformat() + "Z",
                        }
                    )

                if alerts_payload:
                    await session.commit()

            for payload in alerts_payload:
                await ws_manager.broadcast_alert(payload)
        except Exception as exc:
            logger.error("Failed to create alerts: %s", exc)

    async def _roboflow_enrich(
        self,
        frame: np.ndarray,
        tracked_objects: list[dict],
        intrusion_events: list[IntrusionEvent],
    ):
        """Run Roboflow secondary classification in background, store results and alert on threats."""
        try:
            results = await roboflow_classifier.classify_batch(
                frame, tracked_objects, str(self.camera_id)
            )
            if not results:
                return

            # Periodic cleanup of stale cooldowns
            roboflow_classifier.cleanup_cooldowns()

            session_factory = database.get_session_factory()
            alerts_payload: list[dict] = []

            async with session_factory() as session:
                for rf_result in results:
                    top = rf_result.top_prediction
                    if not top:
                        continue

                    # Update detection metadata with Roboflow enrichment
                    from sqlalchemy import select, update as sa_update

                    stmt = (
                        sa_update(Detection)
                        .where(
                            Detection.camera_id == self.camera_id,
                            Detection.object_id == rf_result.object_id,
                        )
                        .order_by(Detection.timestamp.desc())
                        .values(
                            metadata_={
                                "roboflow": rf_result.to_dict(),
                            }
                        )
                    )
                    # SQLite doesn't support ORDER BY in UPDATE, so fetch-then-update
                    sel = (
                        select(Detection.id)
                        .where(
                            Detection.camera_id == str(self.camera_id),
                            Detection.object_id == rf_result.object_id,
                        )
                        .order_by(Detection.timestamp.desc())
                        .limit(1)
                    )
                    row = (await session.execute(sel)).scalar_one_or_none()
                    if row:
                        det = await session.get(Detection, row)
                        if det:
                            existing_meta = dict(det.metadata_ or {})
                            existing_meta["roboflow"] = rf_result.to_dict()
                            det.metadata_ = existing_meta

                    # Create alert when Roboflow classifies a vehicle with high confidence
                    if top.confidence >= 0.5:
                        vehicle_alert = Alert(
                            camera_id=self.camera_id,
                            type="vehicle_detected",
                            severity=AlertSeverity.HIGH.value,
                            trigger_condition=(
                                f"Roboflow classified '{top.class_label}' "
                                f"({top.confidence:.0%}) on object_{rf_result.object_id}"
                            ),
                            description=(
                                f"Vehicle classified on {self.camera_name}: "
                                f"{top.class_label} ({top.confidence:.0%})"
                            ),
                            metadata_={
                                "roboflow": rf_result.to_dict(),
                                "source": "roboflow_secondary_classifier",
                            },
                        )
                        session.add(vehicle_alert)
                        await session.flush()
                        alerts_payload.append({
                            "alert_id": str(vehicle_alert.id),
                            "camera_id": str(self.camera_id),
                            "type": "vehicle_detected",
                            "severity": AlertSeverity.HIGH.value,
                            "roboflow_class": top.class_label,
                            "roboflow_confidence": round(top.confidence, 4),
                            "object_id": rf_result.object_id,
                            "timestamp": utc_now().isoformat() + "Z",
                        })

                if alerts_payload:
                    await session.commit()
                else:
                    await session.commit()

            # Broadcast threat alerts via WebSocket
            for payload in alerts_payload:
                await ws_manager.broadcast_alert(payload)

        except Exception as exc:
            logger.error("Roboflow enrichment failed: %s", exc)

    async def _crime_classify(
        self,
        frame: np.ndarray,
        tracked_objects: list[dict],
        intrusion_events: list[IntrusionEvent],
    ):
        """Run ViT crime classification in background, store results and alert on crimes."""
        try:
            results = await crime_classifier.classify_batch(
                frame, tracked_objects, str(self.camera_id)
            )
            if not results:
                return

            # Periodic cleanup of stale cooldowns
            crime_classifier.cleanup_cooldowns()

            session_factory = database.get_session_factory()
            alerts_payload: list[dict] = []

            async with session_factory() as session:
                for cr_result in results:
                    # Update detection metadata with crime classification
                    from sqlalchemy import select

                    sel = (
                        select(Detection.id)
                        .where(
                            Detection.camera_id == str(self.camera_id),
                            Detection.object_id == cr_result.object_id,
                        )
                        .order_by(Detection.timestamp.desc())
                        .limit(1)
                    )
                    row = (await session.execute(sel)).scalar_one_or_none()
                    if row:
                        det = await session.get(Detection, row)
                        if det:
                            existing_meta = dict(det.metadata_ or {})
                            existing_meta["crime_classifier"] = cr_result.to_dict()
                            det.metadata_ = existing_meta

                    # Create alert when crime is detected with sufficient confidence
                    if (
                        cr_result.prediction == "crime"
                        and cr_result.confidence >= crime_classifier._confidence_threshold
                    ):
                        crime_alert = Alert(
                            camera_id=self.camera_id,
                            type="crime_detected",
                            severity=AlertSeverity.CRITICAL.value,
                            trigger_condition=(
                                f"ViT crime classifier: '{cr_result.prediction}' "
                                f"({cr_result.confidence:.0%}) on object_{cr_result.object_id}"
                            ),
                            description=(
                                f"Criminal activity detected on {self.camera_name}: "
                                f"{cr_result.prediction} ({cr_result.confidence:.0%})"
                            ),
                            metadata_={
                                "crime_classifier": cr_result.to_dict(),
                                "source": "vit_crime_classifier",
                            },
                        )
                        session.add(crime_alert)
                        await session.flush()
                        alerts_payload.append({
                            "alert_id": str(crime_alert.id),
                            "camera_id": str(self.camera_id),
                            "type": "crime_detected",
                            "severity": AlertSeverity.CRITICAL.value,
                            "prediction": cr_result.prediction,
                            "confidence": round(cr_result.confidence, 4),
                            "object_id": cr_result.object_id,
                            "inference_time_ms": round(cr_result.inference_time_ms, 1),
                            "timestamp": utc_now().isoformat() + "Z",
                        })

                await session.commit()

            # Broadcast crime alerts via WebSocket
            for alert_payload in alerts_payload:
                await ws_manager.broadcast_alert(alert_payload)

        except Exception as exc:
            logger.error("Crime classification failed: %s", exc)

    def get_status(self) -> dict:
        return {
            "camera_id": str(self.camera_id),
            "camera_name": self.camera_name,
            "is_running": self._running,
            "is_paused": self._paused,
            "is_video_source": self.is_video_source,
            "fps": round(self._fps, 1),
            "frame_count": self._frame_count,
            "active_tracks": len(self._last_detections),
            "uptime_seconds": round(time.time() - self._start_time, 1) if self._running else 0,
            "current_frame_skip": self._current_frame_skip,
        }


class StreamManager:
    """Manages all active video streams."""

    def __init__(self):
        self._streams: dict[str, VideoStream] = {}
        self._inference_pool = None
        self._settings = get_settings()

    def set_inference_pool(self, pool):
        """Injected by main.py at startup."""
        self._inference_pool = pool

    def _mediamtx_url(self, path: str) -> str:
        base = self._settings.MEDIAMTX_API_BASE_URL.rstrip("/")
        return f"{base}{path}"

    def _mediamtx_auth(self) -> tuple[str, str] | None:
        username = (self._settings.MEDIAMTX_API_USERNAME or "").strip()
        if not username:
            return None
        return (username, self._settings.MEDIAMTX_API_PASSWORD or "")

    async def start_stream(self, camera: Camera) -> bool:
        if camera.id in self._streams:
            existing = self._streams[camera.id]
            if existing._running:
                return True
            await existing.stop()
            del self._streams[camera.id]

        if not self._inference_pool:
            logger.error("Cannot start stream: Inference pool not initialized")
            return False

        if self._settings.MEDIAMTX_ENABLED:
            # Register stream with MediaMTX for WebRTC playback.
            try:
                import httpx

                async with httpx.AsyncClient(
                    timeout=self._settings.MEDIAMTX_REQUEST_TIMEOUT_SECONDS,
                ) as client:
                    res = await client.post(
                        self._mediamtx_url(f"/v3/config/paths/add/{camera.id}"),
                        auth=self._mediamtx_auth(),
                        json={"source": camera.stream_url},
                    )
                    if res.status_code not in (200, 400):  # 400 means path already exists
                        logger.warning("MediaMTX path registration failed: %s", res.text)
            except Exception as e:
                logger.warning("MediaMTX API error (add path): %s", e)

        stream = VideoStream(camera, self._inference_pool)
        started = await stream.start()
        if not started:
            return False

        self._streams[camera.id] = stream
        return True

    async def stop_stream(self, camera_id: str):
        if camera_id in self._streams:
            await self._streams[camera_id].stop()
            del self._streams[camera_id]

        if self._settings.MEDIAMTX_ENABLED:
            try:
                import httpx

                async with httpx.AsyncClient(
                    timeout=self._settings.MEDIAMTX_REQUEST_TIMEOUT_SECONDS,
                ) as client:
                    await client.delete(
                        self._mediamtx_url(f"/v3/config/paths/delete/{camera_id}"),
                        auth=self._mediamtx_auth(),
                    )
            except Exception as e:
                logger.warning("MediaMTX API error (delete path): %s", e)

    async def stop_all(self):
        for stream in self._streams.values():
            await stream.stop()
        self._streams.clear()

    def pause_stream(self, camera_id: str) -> bool:
        stream = self._streams.get(camera_id)
        if not stream or not stream._running:
            return False
        if not stream.is_video_source:
            return False
        stream.pause()
        return True

    def resume_stream(self, camera_id: str) -> bool:
        stream = self._streams.get(camera_id)
        if not stream or not stream._running:
            return False
        stream.resume()
        return True

    def get_stream_status(self, camera_id: str) -> Optional[dict]:
        if camera_id in self._streams:
            return self._streams[camera_id].get_status()
        return None

    def get_all_status(self) -> list[dict]:
        return [stream.get_status() for stream in self._streams.values()]

    @property
    def active_count(self) -> int:
        return sum(1 for stream in self._streams.values() if stream._running)


stream_manager = StreamManager()
