"""Roboflow secondary classifier — enriches YOLO detections via Roboflow Inference API.

Runs asynchronously and never blocks the primary detection pipeline.
Crops detected objects from frames and sends them to a Roboflow model
for secondary classification (e.g. weapon detection, PPE check).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoboflowPrediction:
    """A single prediction returned by the Roboflow API."""

    class_label: str
    confidence: float
    x: float
    y: float
    width: float
    height: float


@dataclass
class RoboflowResult:
    """Result of a Roboflow classification for one tracked object."""

    object_id: int
    camera_id: str
    yolo_class: str
    predictions: list[RoboflowPrediction]
    inference_time_ms: float
    timestamp: float = field(default_factory=time.time)

    @property
    def top_prediction(self) -> RoboflowPrediction | None:
        if not self.predictions:
            return None
        return max(self.predictions, key=lambda p: p.confidence)

    def to_dict(self) -> dict:
        top = self.top_prediction
        return {
            "object_id": self.object_id,
            "camera_id": self.camera_id,
            "yolo_class": self.yolo_class,
            "predictions": [
                {
                    "class": p.class_label,
                    "confidence": round(p.confidence, 4),
                    "x": round(p.x, 1),
                    "y": round(p.y, 1),
                    "width": round(p.width, 1),
                    "height": round(p.height, 1),
                }
                for p in self.predictions
            ],
            "top_class": top.class_label if top else None,
            "top_confidence": round(top.confidence, 4) if top else None,
            "inference_time_ms": round(self.inference_time_ms, 1),
        }


class RoboflowClassifier:
    """Async Roboflow inference client with rate limiting and cooldown."""

    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = settings.ROBOFLOW_ENABLED
        self._api_key = settings.ROBOFLOW_API_KEY
        self._model_id = settings.ROBOFLOW_MODEL_ID
        self._model_version = settings.ROBOFLOW_MODEL_VERSION
        self._confidence = settings.ROBOFLOW_CONFIDENCE
        self._trigger_classes = {c.strip().lower() for c in settings.ROBOFLOW_TRIGGER_CLASSES}
        self._cooldown_sec = settings.ROBOFLOW_COOLDOWN_SEC
        self._api_url = settings.ROBOFLOW_API_URL.rstrip("/")

        # Concurrency limiter
        self._semaphore = asyncio.Semaphore(settings.ROBOFLOW_MAX_CONCURRENT)

        # Cooldown tracker: (camera_id, object_id) -> last_classified_time
        self._cooldown_map: dict[tuple[str, int], float] = {}

        # Metrics
        self._total_requests = 0
        self._total_errors = 0
        self._total_predictions = 0
        self._last_error: str | None = None

        if self._enabled:
            if not self._api_key:
                logger.warning("Roboflow enabled but ROBOFLOW_API_KEY is empty — disabling")
                self._enabled = False
            elif not self._model_id:
                logger.warning("Roboflow enabled but ROBOFLOW_MODEL_ID is empty — disabling")
                self._enabled = False
            else:
                logger.info(
                    "Roboflow classifier initialized: model=%s/%s, triggers=%s",
                    self._model_id,
                    self._model_version,
                    self._trigger_classes,
                )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def should_classify(self, camera_id: str, object_id: int, class_label: str) -> bool:
        """Check if this object should be sent to Roboflow."""
        if not self._enabled:
            return False
        if class_label.strip().lower() not in self._trigger_classes:
            return False
        key = (camera_id, object_id)
        last_time = self._cooldown_map.get(key, 0.0)
        if time.time() - last_time < self._cooldown_sec:
            return False
        return True

    def _crop_object(self, frame: np.ndarray, obj: dict, padding: float = 0.1) -> np.ndarray:
        """Crop a tracked object from the frame with optional padding."""
        h, w = frame.shape[:2]
        bx = obj.get("bbox_x", 0)
        by = obj.get("bbox_y", 0)
        bw = obj.get("bbox_w", 0)
        bh = obj.get("bbox_h", 0)

        pad_x = bw * padding
        pad_y = bh * padding

        x1 = max(0, int(bx - pad_x))
        y1 = max(0, int(by - pad_y))
        x2 = min(w, int(bx + bw + pad_x))
        y2 = min(h, int(by + bh + pad_y))

        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return frame  # fallback to full frame
        return crop

    def _encode_image(self, image: np.ndarray) -> str:
        """Encode image to base64 JPEG for Roboflow API."""
        success, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not success:
            raise ValueError("Failed to encode image to JPEG")
        return base64.b64encode(buffer.tobytes()).decode("ascii")

    async def classify_object(
        self,
        frame: np.ndarray,
        tracked_obj: dict,
        camera_id: str,
    ) -> RoboflowResult | None:
        """Send a cropped detection to Roboflow for secondary classification.

        Returns None if classification is skipped or fails.
        """
        object_id = tracked_obj.get("object_id", -1)
        class_label = tracked_obj.get("class_label", "")

        if not self.should_classify(camera_id, object_id, class_label):
            return None

        async with self._semaphore:
            try:
                import httpx

                crop = self._crop_object(frame, tracked_obj)
                encoded = self._encode_image(crop)

                url = f"{self._api_url}/{self._model_id}/{self._model_version}"
                params = {
                    "api_key": self._api_key,
                    "confidence": str(self._confidence),
                }

                t_start = time.perf_counter()

                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(
                        url,
                        params=params,
                        content=encoded,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )

                elapsed_ms = (time.perf_counter() - t_start) * 1000
                self._total_requests += 1

                if response.status_code != 200:
                    self._total_errors += 1
                    self._last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(
                        "Roboflow API error for object %d: %s",
                        object_id,
                        self._last_error,
                    )
                    return None

                data = response.json()
                predictions = [
                    RoboflowPrediction(
                        class_label=p["class"],
                        confidence=p["confidence"],
                        x=p.get("x", 0),
                        y=p.get("y", 0),
                        width=p.get("width", 0),
                        height=p.get("height", 0),
                    )
                    for p in data.get("predictions", [])
                ]

                self._total_predictions += len(predictions)
                self._cooldown_map[(camera_id, object_id)] = time.time()

                result = RoboflowResult(
                    object_id=object_id,
                    camera_id=camera_id,
                    yolo_class=class_label,
                    predictions=predictions,
                    inference_time_ms=elapsed_ms,
                )

                if predictions:
                    top = result.top_prediction
                    logger.info(
                        "Roboflow [%s] object_%d: %s (%.1f%%) in %.0fms",
                        camera_id,
                        object_id,
                        top.class_label,
                        top.confidence * 100,
                        elapsed_ms,
                    )

                return result

            except Exception as exc:
                self._total_errors += 1
                self._last_error = str(exc)
                logger.error("Roboflow classification failed for object %d: %s", object_id, exc)
                return None

    async def classify_batch(
        self,
        frame: np.ndarray,
        tracked_objects: list[dict],
        camera_id: str,
    ) -> list[RoboflowResult]:
        """Classify all eligible tracked objects in parallel."""
        if not self._enabled or not tracked_objects:
            return []

        tasks = [
            self.classify_object(frame, obj, camera_id)
            for obj in tracked_objects
            if self.should_classify(camera_id, obj.get("object_id", -1), obj.get("class_label", ""))
        ]

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, RoboflowResult)]

    def cleanup_cooldowns(self, max_age_sec: float = 300.0) -> None:
        """Remove stale cooldown entries to prevent memory growth."""
        now = time.time()
        stale_keys = [k for k, t in self._cooldown_map.items() if now - t > max_age_sec]
        for k in stale_keys:
            del self._cooldown_map[k]

    def get_status(self) -> dict:
        """Return current status and metrics."""
        return {
            "enabled": self._enabled,
            "model_id": self._model_id,
            "model_version": self._model_version,
            "confidence_threshold": self._confidence,
            "trigger_classes": sorted(self._trigger_classes),
            "cooldown_sec": self._cooldown_sec,
            "total_requests": self._total_requests,
            "total_errors": self._total_errors,
            "total_predictions": self._total_predictions,
            "active_cooldowns": len(self._cooldown_map),
            "last_error": self._last_error,
        }


# Module-level singleton
roboflow_classifier = RoboflowClassifier()
