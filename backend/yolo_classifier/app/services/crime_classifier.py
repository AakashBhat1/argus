"""ViT Crime Classifier — local secondary classifier for intrusion events.

Runs the Nikeytas/google-vit-best-crime-detector model locally via PyTorch
to classify cropped person detections as 'crime' or 'normal'. Only triggered
when YOLO detects a person inside an ROI zone and the dwell threshold is
exceeded (i.e., an intrusion event fires).

Follows the same architectural pattern as RoboflowClassifier:
  - Async interface with cooldown per tracked object
  - Non-blocking background task in stream_manager
  - Thread-pool inference (torch releases GIL during C++ ops)
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

_MODEL_DOWNLOAD_FLAG = "model_downloaded.flag"


def _backend_root() -> Path:
    """Return the backend/ directory."""
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CrimeResult:
    """Result of a ViT crime classification for one tracked object."""

    object_id: int
    camera_id: str
    prediction: str  # "crime" or "normal"
    confidence: float
    inference_time_ms: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "object_id": self.object_id,
            "camera_id": self.camera_id,
            "prediction": self.prediction,
            "confidence": round(self.confidence, 4),
            "inference_time_ms": round(self.inference_time_ms, 1),
        }


class CrimeClassifier:
    """Local ViT-based crime classifier with lazy model loading."""

    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = settings.CRIME_CLASSIFIER_ENABLED
        self._model_id = settings.CRIME_CLASSIFIER_MODEL_ID
        self._confidence_threshold = settings.CRIME_CLASSIFIER_CONFIDENCE
        self._trigger_classes = {
            c.strip().lower() for c in settings.CRIME_CLASSIFIER_TRIGGER_CLASSES
        }
        self._cooldown_sec = settings.CRIME_CLASSIFIER_COOLDOWN_SEC
        self._device_name = settings.CRIME_CLASSIFIER_DEVICE
        self._cache_dir = _backend_root() / settings.CRIME_CLASSIFIER_CACHE_DIR

        # Concurrency limiter
        self._semaphore = asyncio.Semaphore(settings.CRIME_CLASSIFIER_MAX_CONCURRENT)

        # Cooldown tracker: (camera_id, object_id) -> last_classified_time
        self._cooldown_map: dict[tuple[str, int], float] = {}

        # Model state (lazy loaded)
        self._model = None
        self._transform = None
        self._device = None
        self._load_lock = threading.Lock()
        self._model_loaded = False

        # Metrics
        self._total_classifications = 0
        self._total_crimes_detected = 0
        self._total_errors = 0
        self._last_error: Optional[str] = None

        if not self._enabled:
            logger.info("Crime classifier disabled via CRIME_CLASSIFIER_ENABLED=false")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _is_model_downloaded(self) -> bool:
        """Check the download flag file to see if model was already fetched."""
        flag_path = self._cache_dir / _MODEL_DOWNLOAD_FLAG
        return flag_path.exists()

    def _mark_model_downloaded(self) -> None:
        """Write the download flag file after successful download."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        flag_path = self._cache_dir / _MODEL_DOWNLOAD_FLAG
        flag_path.write_text("1", encoding="utf-8")
        logger.info("Model download flag written: %s", flag_path)

    def _download_model(self) -> Path:
        """Download model from HuggingFace Hub if not already cached."""
        from huggingface_hub import hf_hub_download

        self._cache_dir.mkdir(parents=True, exist_ok=True)

        if self._is_model_downloaded():
            # Model already downloaded — find the cached .pth file
            model_path = self._cache_dir / "model.pth"
            if model_path.exists():
                logger.info("Model already downloaded (flag=1), skipping download")
                return model_path
            logger.warning(
                "Download flag exists but model.pth missing — re-downloading"
            )

        logger.info(
            "Downloading crime detection model: %s (first-time only)", self._model_id
        )
        downloaded_path = hf_hub_download(
            repo_id=self._model_id,
            filename="model.pth",
            cache_dir=str(self._cache_dir / "hf_cache"),
        )

        # Copy to a stable path for easy access
        import shutil

        model_path = self._cache_dir / "model.pth"
        shutil.copy2(downloaded_path, model_path)

        self._mark_model_downloaded()
        logger.info("Model downloaded and cached at: %s", model_path)
        return model_path

    def _load_model(self) -> None:
        """Lazy, thread-safe model loading."""
        if self._model_loaded:
            return

        with self._load_lock:
            if self._model_loaded:
                return

            try:
                import torch
                import torchvision.transforms as transforms

                model_path = self._download_model()

                self._device = torch.device(self._device_name)
                self._model = torch.load(
                    str(model_path),
                    map_location=self._device,
                    weights_only=False,
                )
                self._model.eval()

                self._transform = transforms.Compose(
                    [
                        transforms.ToPILImage(),
                        transforms.Resize((224, 224)),
                        transforms.ToTensor(),
                        transforms.Normalize(
                            mean=[0.485, 0.456, 0.406],
                            std=[0.229, 0.224, 0.225],
                        ),
                    ]
                )

                self._model_loaded = True
                logger.info(
                    "Crime classifier model loaded: %s on %s",
                    self._model_id,
                    self._device,
                )
            except Exception as exc:
                self._last_error = f"Model load failed: {exc}"
                logger.error("Failed to load crime classifier model: %s", exc)
                self._enabled = False

    def should_classify(
        self,
        camera_id: str,
        object_id: int,
        class_label: str,
        has_intrusion: bool,
    ) -> bool:
        """Check if this object should be sent to the crime classifier."""
        if not self._enabled:
            return False
        if not has_intrusion:
            return False
        if class_label.strip().lower() not in self._trigger_classes:
            return False
        key = (camera_id, object_id)
        last_time = self._cooldown_map.get(key, 0.0)
        if time.time() - last_time < self._cooldown_sec:
            return False
        return True

    def _crop_object(
        self, frame: np.ndarray, obj: dict, padding: float = 0.1
    ) -> np.ndarray:
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
            return frame
        return crop

    def _classify_sync(self, frame_rgb: np.ndarray) -> tuple[str, float]:
        """Run synchronous inference. Called from thread pool."""
        import torch

        self._load_model()

        tensor = self._transform(frame_rgb)
        tensor = tensor.unsqueeze(0).to(self._device)

        with torch.no_grad():
            outputs = self._model(tensor)
            probabilities = torch.softmax(outputs, dim=1)
            predicted_class = torch.argmax(probabilities, dim=1).item()
            confidence = torch.max(probabilities, dim=1)[0].item()

        prediction = "crime" if predicted_class == 1 else "normal"
        return prediction, confidence

    async def classify_object(
        self,
        frame: np.ndarray,
        tracked_obj: dict,
        camera_id: str,
    ) -> Optional[CrimeResult]:
        """Classify a single tracked object for criminal activity.

        Returns None if classification is skipped or fails.
        """
        object_id = tracked_obj.get("object_id", -1)
        class_label = tracked_obj.get("class_label", "")
        has_intrusion = bool(tracked_obj.get("intrusion", False))

        if not self.should_classify(camera_id, object_id, class_label, has_intrusion):
            return None

        async with self._semaphore:
            try:
                crop = self._crop_object(frame, tracked_obj)
                # Convert BGR (OpenCV) to RGB for torchvision transforms
                crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

                loop = asyncio.get_event_loop()
                t_start = time.perf_counter()

                prediction, confidence = await loop.run_in_executor(
                    None, self._classify_sync, crop_rgb
                )

                elapsed_ms = (time.perf_counter() - t_start) * 1000
                self._total_classifications += 1
                self._cooldown_map[(camera_id, object_id)] = time.time()

                result = CrimeResult(
                    object_id=object_id,
                    camera_id=camera_id,
                    prediction=prediction,
                    confidence=confidence,
                    inference_time_ms=elapsed_ms,
                )

                if prediction == "crime" and confidence >= self._confidence_threshold:
                    self._total_crimes_detected += 1
                    logger.warning(
                        "CRIME detected [%s] object_%d: %s (%.1f%%) in %.0fms",
                        camera_id,
                        object_id,
                        prediction,
                        confidence * 100,
                        elapsed_ms,
                    )
                else:
                    logger.info(
                        "Crime classifier [%s] object_%d: %s (%.1f%%) in %.0fms",
                        camera_id,
                        object_id,
                        prediction,
                        confidence * 100,
                        elapsed_ms,
                    )

                return result

            except Exception as exc:
                self._total_errors += 1
                self._last_error = str(exc)
                logger.error(
                    "Crime classification failed for object %d: %s", object_id, exc
                )
                return None

    async def classify_batch(
        self,
        frame: np.ndarray,
        tracked_objects: list[dict],
        camera_id: str,
    ) -> list[CrimeResult]:
        """Classify all eligible tracked objects (those with active intrusions)."""
        if not self._enabled or not tracked_objects:
            return []

        tasks = [
            self.classify_object(frame, obj, camera_id)
            for obj in tracked_objects
            if self.should_classify(
                camera_id,
                obj.get("object_id", -1),
                obj.get("class_label", ""),
                bool(obj.get("intrusion", False)),
            )
        ]

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, CrimeResult)]

    def cleanup_cooldowns(self, max_age_sec: float = 300.0) -> None:
        """Remove stale cooldown entries to prevent memory growth."""
        now = time.time()
        stale_keys = [
            k for k, t in self._cooldown_map.items() if now - t > max_age_sec
        ]
        for k in stale_keys:
            del self._cooldown_map[k]

    def get_status(self) -> dict:
        """Return current status and metrics."""
        return {
            "enabled": self._enabled,
            "model_id": self._model_id,
            "model_loaded": self._model_loaded,
            "model_downloaded": self._is_model_downloaded(),
            "confidence_threshold": self._confidence_threshold,
            "trigger_classes": sorted(self._trigger_classes),
            "cooldown_sec": self._cooldown_sec,
            "device": self._device_name,
            "total_classifications": self._total_classifications,
            "total_crimes_detected": self._total_crimes_detected,
            "total_errors": self._total_errors,
            "active_cooldowns": len(self._cooldown_map),
            "last_error": self._last_error,
        }


# Module-level singleton
crime_classifier = CrimeClassifier()
