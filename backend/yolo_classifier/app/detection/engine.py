"""OpenVINO-backed YOLO detector (supports YOLO26 end-to-end and legacy YOLOv8)."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

COCO_CLASSES = [
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
]

_REQUIRED_COCO_CLASS_MAPPING = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    
}


def _nms_numpy(
    boxes: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float = 0.5,
) -> np.ndarray:
    """Run non-maximum suppression using NumPy."""
    if len(boxes) == 0:
        return np.array([], dtype=int)

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = (x2 - x1) * (y2 - y1)

    order = scores.argsort()[::-1]
    keep: list[int] = []

    while order.size > 0:
        i = int(order[0])
        keep.append(i)

        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        intersection = w * h
        iou = intersection / (areas[i] + areas[order[1:]] - intersection + 1e-6)

        inds = np.where(iou <= iou_threshold)[0]
        order = order[inds + 1]

    return np.array(keep, dtype=int)


def _classwise_nms_numpy(
    boxes: np.ndarray,
    scores: np.ndarray,
    class_ids: np.ndarray,
    iou_threshold: float,
) -> np.ndarray:
    """Run class-wise NMS to avoid suppressing overlapping objects of different classes."""
    if len(boxes) == 0:
        return np.array([], dtype=int)

    keep_indices: list[int] = []
    for class_id in np.unique(class_ids):
        class_mask = class_ids == class_id
        class_indices = np.where(class_mask)[0]
        class_keep_local = _nms_numpy(
            boxes[class_indices],
            scores[class_indices],
            iou_threshold=iou_threshold,
        )
        keep_indices.extend(class_indices[class_keep_local].tolist())

    # Deterministic ordering across runs.
    keep_indices.sort(
        key=lambda idx: (
            -float(scores[idx]),
            int(class_ids[idx]),
            float(boxes[idx][0]),
            float(boxes[idx][1]),
        )
    )
    return np.array(keep_indices, dtype=int)


class OpenVINODetector:
    """Thread-safe OpenVINO detector with a backward-compatible API."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        device: Optional[str] = None,
        confidence_threshold: Optional[float] = None,
        nms_iou_threshold: Optional[float] = None,
        input_size: Optional[tuple[int, int]] = None,
    ):
        from app.config import get_settings

        settings = get_settings()
        self._model_path = model_path or settings.OPENVINO_MODEL_PATH
        self._device = device or settings.OPENVINO_DEVICE
        self._precision_setting = (settings.OPENVINO_PRECISION or "").strip().upper()
        self._conf_threshold = (
            float(confidence_threshold)
            if confidence_threshold is not None
            else float(settings.YOLO_CONFIDENCE)
        )
        self._nms_iou = (
            float(nms_iou_threshold)
            if nms_iou_threshold is not None
            else float(settings.YOLO_NMS_IOU)
        )
        self._input_h, self._input_w = input_size or (640, 640)
        self._raw_detection_logs_enabled = bool(settings.YOLO_LOG_RAW_DETECTIONS)
        self._raw_detection_log_limit = max(1, int(settings.YOLO_RAW_DETECTIONS_MAX_LOG))

        self._compiled_model = None
        self._input_layer = None
        self._output_layer = None
        self._class_names = list(COCO_CLASSES)
        self._class_id_to_name = {idx: name for idx, name in enumerate(self._class_names)}
        self._class_name_to_id = {name.lower(): idx for idx, name in enumerate(self._class_names)}
        configured_allowed = {
            str(item).strip().lower()
            for item in settings.ALLOWED_CLASSES
            if str(item).strip()
        }
        if not configured_allowed:
            configured_allowed = {"person"}
        unknown_allowed = sorted(configured_allowed - set(self._class_name_to_id))
        if unknown_allowed:
            raise ValueError(
                "ALLOWED_CLASSES contains names not found in detector class mapping: "
                + ", ".join(unknown_allowed)
            )
        self._allowed_class_names = configured_allowed
        self._allowed_class_ids = {
            self._class_name_to_id[class_name] for class_name in self._allowed_class_names
        }
        raw_class_thresholds = dict(settings.YOLO_CLASS_CONFIDENCE_THRESHOLDS or {})
        self._per_class_threshold = {
            str(class_name).strip().lower(): min(max(float(threshold), 0.0), 1.0)
            for class_name, threshold in raw_class_thresholds.items()
            if str(class_name).strip()
        }
        self._actual_device = "unknown"
        self._resolved_model_path = ""

        self._last_inference_ms = 0.0
        self._last_preprocess_ms = 0.0
        self._last_postprocess_ms = 0.0
        self._total_inferences = 0

        self._load_model()

    def _load_model(self) -> None:
        try:
            import openvino as ov
        except ImportError as exc:  # pragma: no cover - import error path
            raise ImportError(
                "OpenVINO Runtime not found. Install with: pip install openvino"
            ) from exc

        core = ov.Core()
        model_path = Path(self._model_path)
        if not model_path.is_absolute():
            backend_dir = Path(__file__).resolve().parent.parent.parent
            model_path = backend_dir / model_path

        if not model_path.exists():
            raise FileNotFoundError(
                f"OpenVINO model not found: {model_path}. "
                "Run model conversion before startup."
            )

        self._resolved_model_path = str(model_path.resolve())
        logger.info("Loading OpenVINO model: %s", model_path)
        model = core.read_model(str(model_path))
        self._compiled_model = self._compile_with_fallback(core, model)
        self._input_layer = self._compiled_model.input(0)
        self._output_layer = self._compiled_model.output(0)
        self._validate_class_mapping()
        self._validate_model_output_shape()
        logger.info(
            "Allowed detector classes: %s",
            ", ".join(
                f"{class_id}:{self._class_id_to_name[class_id]}"
                for class_id in sorted(self._allowed_class_ids)
            ),
        )

        logger.info(
            "Model compiled on device=%s input=%s output=%s",
            self._actual_device,
            self._input_layer.shape,
            self._output_layer.shape,
        )

    def _compile_with_fallback(self, core, model):
        devices_to_try = [self._device]
        if self._device != "AUTO":
            devices_to_try.append("AUTO")
        if self._device != "CPU":
            devices_to_try.append("CPU")

        logger.info("OpenVINO available devices: %s", core.available_devices)
        last_error: Exception | None = None
        for device in devices_to_try:
            try:
                config = {}
                if device in ("CPU", "AUTO", "GPU"):
                    config["PERFORMANCE_HINT"] = "THROUGHPUT"
                compiled = core.compile_model(model, device, config)
                self._actual_device = device
                logger.info("Successfully compiled model on %s", device)
                return compiled
            except Exception as exc:  # pragma: no cover - hardware-dependent
                logger.warning("Failed to compile model on %s: %s", device, exc)
                last_error = exc
        raise RuntimeError(
            f"Failed to compile model on devices {devices_to_try}. Last error: {last_error}"
        )

    def _validate_class_mapping(self) -> None:
        mismatches: list[str] = []
        for class_id, expected_name in _REQUIRED_COCO_CLASS_MAPPING.items():
            if class_id >= len(self._class_names):
                mismatches.append(
                    f"id {class_id}: expected '{expected_name}', class list has {len(self._class_names)} entries"
                )
                continue
            actual_name = str(self._class_names[class_id]).strip().lower()
            if actual_name != expected_name:
                mismatches.append(
                    f"id {class_id}: expected '{expected_name}', got '{self._class_names[class_id]}'"
                )

        if mismatches:
            mismatch_text = "; ".join(mismatches)
            raise RuntimeError(
                "YOLO class index->label mapping validation failed. "
                f"Detected mapping mismatch: {mismatch_text}"
            )

        logger.info(
            "Verified COCO class mapping for required classes: %s",
            ", ".join(f"{idx}:{name}" for idx, name in sorted(_REQUIRED_COCO_CLASS_MAPPING.items())),
        )

    def _detect_model_format(self) -> str:
        """Detect whether the model is end-to-end (YOLO26+) or legacy (YOLOv8)."""
        try:
            output_shape = tuple(int(dim) for dim in self._output_layer.shape)
        except Exception:
            return "legacy"
        # End-to-end models output [batch, max_dets, 6] where 6 = [x1,y1,x2,y2,conf,class_id]
        if len(output_shape) == 3 and output_shape[2] == 6:
            return "e2e"
        return "legacy"

    def _infer_num_classes_from_output_shape(self, output_shape: tuple[int, ...]) -> Optional[int]:
        if len(output_shape) != 3:
            return None
        batch_dim, dim_a, dim_b = output_shape
        del batch_dim
        candidates = [dim - 4 for dim in (dim_a, dim_b) if dim > 4]
        if candidates:
            return min(candidates)
        return None

    def _validate_model_output_shape(self) -> None:
        self._model_format = self._detect_model_format()
        logger.info("Detected model format: %s", self._model_format)
        if self._model_format == "e2e":
            # End-to-end models don't need class count validation
            return
        try:
            output_shape = tuple(int(dim) for dim in self._output_layer.shape)
        except Exception:
            logger.warning(
                "Unable to read static output shape from model output layer: %s",
                self._output_layer.shape,
            )
            return
        inferred_num_classes = self._infer_num_classes_from_output_shape(output_shape)
        if inferred_num_classes is None:
            logger.warning(
                "Could not infer number of classes from output shape %s. "
                "Continuing without class-count validation.",
                output_shape,
            )
            return

        expected_num_classes = len(self._class_names)
        if inferred_num_classes != expected_num_classes:
            raise RuntimeError(
                "Model output class count does not match class-name mapping. "
                f"output_shape={output_shape} inferred={inferred_num_classes} "
                f"class_names={expected_num_classes} model={self._resolved_model_path}"
            )

    def _preprocess(self, frame: np.ndarray) -> tuple[np.ndarray, dict]:
        orig_h, orig_w = frame.shape[:2]
        scale = min(self._input_h / orig_h, self._input_w / orig_w)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)

        import cv2

        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        pad_y = (self._input_h - new_h) // 2
        pad_x = (self._input_w - new_w) // 2

        canvas = np.full((self._input_h, self._input_w, 3), 114, dtype=np.uint8)
        canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized

        blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        blob = np.ascontiguousarray(blob)
        blob = np.expand_dims(blob, axis=0)

        return blob, {
            "orig_h": orig_h,
            "orig_w": orig_w,
            "scale": scale,
            "pad_x": pad_x,
            "pad_y": pad_y,
        }

    def _preprocess_batch(self, frames: list[np.ndarray]) -> tuple[np.ndarray, list[dict]]:
        blobs: list[np.ndarray] = []
        metas: list[dict] = []
        for frame in frames:
            blob, meta = self._preprocess(frame)
            blobs.append(blob)
            metas.append(meta)
        return np.ascontiguousarray(np.concatenate(blobs, axis=0)), metas

    def _threshold_for_class_id(self, class_id: int) -> float:
        class_name = self._class_id_to_name.get(class_id, "").lower()
        return float(self._per_class_threshold.get(class_name, self._conf_threshold))

    def _log_raw_candidates(
        self,
        boxes: np.ndarray,
        class_ids: np.ndarray,
        scores: np.ndarray,
    ) -> None:
        if not self._raw_detection_logs_enabled or not logger.isEnabledFor(logging.DEBUG):
            return
        if len(scores) == 0:
            logger.debug("Raw detections before filtering: none")
            return

        limit = min(len(scores), self._raw_detection_log_limit)
        top_indices = scores.argsort()[::-1][:limit]
        payload: list[dict] = []
        for idx in top_indices:
            class_id = int(class_ids[idx])
            cx, cy, w, h = boxes[idx]
            payload.append(
                {
                    "class_id": class_id,
                    "class_label": self._class_id_to_name.get(class_id, f"class_{class_id}"),
                    "score": round(float(scores[idx]), 4),
                    "bbox_cxcywh": [
                        round(float(cx), 2),
                        round(float(cy), 2),
                        round(float(w), 2),
                        round(float(h), 2),
                    ],
                }
            )
        logger.debug("Raw detections before filtering (top=%s): %s", limit, payload)

    def _postprocess_e2e(
        self,
        output: np.ndarray,
        meta: dict,
        conf_threshold: Optional[float] = None,
    ) -> list[dict]:
        """Postprocess end-to-end model output [batch, max_dets, 6].
        Each row: [x1, y1, x2, y2, confidence, class_id].
        No NMS needed — model already handles it.
        """
        conf_threshold = (
            float(conf_threshold) if conf_threshold is not None else self._conf_threshold
        )

        if output.ndim == 3:
            output = output[0]  # Remove batch dim -> [max_dets, 6]

        if output.ndim != 2 or output.shape[1] != 6:
            logger.warning("Unexpected e2e output shape: %s", output.shape)
            return []

        boxes_xyxy = output[:, :4]
        scores = output[:, 4]
        class_ids = output[:, 5].astype(int)

        # Filter by confidence (per-class thresholds)
        thresholds = np.array(
            [max(conf_threshold, self._threshold_for_class_id(int(cid))) for cid in class_ids],
            dtype=np.float32,
        )
        conf_mask = scores >= thresholds
        # Filter by allowed classes
        allowed_mask = np.isin(class_ids, list(self._allowed_class_ids))
        mask = conf_mask & allowed_mask
        if not mask.any():
            return []

        boxes_xyxy = boxes_xyxy[mask]
        scores = scores[mask]
        class_ids = class_ids[mask]

        # Rescale from padded input coords to original image coords
        scale = meta["scale"]
        pad_x = meta["pad_x"]
        pad_y = meta["pad_y"]

        boxes_xyxy[:, 0] = (boxes_xyxy[:, 0] - pad_x) / scale
        boxes_xyxy[:, 1] = (boxes_xyxy[:, 1] - pad_y) / scale
        boxes_xyxy[:, 2] = (boxes_xyxy[:, 2] - pad_x) / scale
        boxes_xyxy[:, 3] = (boxes_xyxy[:, 3] - pad_y) / scale

        boxes_xyxy[:, 0] = np.clip(boxes_xyxy[:, 0], 0, meta["orig_w"])
        boxes_xyxy[:, 1] = np.clip(boxes_xyxy[:, 1], 0, meta["orig_h"])
        boxes_xyxy[:, 2] = np.clip(boxes_xyxy[:, 2], 0, meta["orig_w"])
        boxes_xyxy[:, 3] = np.clip(boxes_xyxy[:, 3], 0, meta["orig_h"])

        detections: list[dict] = []
        for i in range(len(boxes_xyxy)):
            x1_f, y1_f, x2_f, y2_f = boxes_xyxy[i]
            class_id = int(class_ids[i])
            class_name = (
                self._class_names[class_id]
                if class_id < len(self._class_names)
                else f"class_{class_id}"
            )
            detections.append(
                {
                    "class_id": class_id,
                    "class_label": class_name,
                    "confidence": round(float(scores[i]), 4),
                    "bbox": [
                        float(x1_f),
                        float(y1_f),
                        float(x2_f - x1_f),
                        float(y2_f - y1_f),
                    ],
                    "bbox_xyxy": [float(x1_f), float(y1_f), float(x2_f), float(y2_f)],
                }
            )

        detections.sort(
            key=lambda det: (
                -float(det["confidence"]),
                int(det["class_id"]),
                float(det["bbox"][0]),
                float(det["bbox"][1]),
            )
        )
        return detections

    def _postprocess(
        self,
        output: np.ndarray,
        meta: dict,
        conf_threshold: Optional[float] = None,
        nms_iou: Optional[float] = None,
    ) -> list[dict]:
        # Dispatch to end-to-end postprocessor if model is YOLO26+
        if getattr(self, "_model_format", "legacy") == "e2e":
            return self._postprocess_e2e(output, meta, conf_threshold)

        conf_threshold = (
            float(conf_threshold) if conf_threshold is not None else self._conf_threshold
        )
        nms_iou = float(nms_iou) if nms_iou is not None else self._nms_iou

        if output.ndim == 3:
            output = output[0]
        if output.ndim != 2:
            logger.warning("Unexpected detector output ndim=%s shape=%s", output.ndim, output.shape)
            return []

        class_channels = len(self._class_names) + 4
        if output.shape[0] == class_channels:
            predictions = output.T
        elif output.shape[1] == class_channels:
            predictions = output
        else:
            predictions = output.T if output.shape[0] < output.shape[1] else output
            logger.debug(
                "Fallback output reshape applied. output_shape=%s predictions_shape=%s",
                output.shape,
                predictions.shape,
            )

        boxes_cxcywh = predictions[:, :4]
        class_scores = predictions[:, 4:]

        max_scores = class_scores.max(axis=1)
        class_ids = class_scores.argmax(axis=1)
        self._log_raw_candidates(boxes_cxcywh, class_ids, max_scores)

        allowed_mask = np.isin(class_ids, list(self._allowed_class_ids))
        if not allowed_mask.any():
            return []

        boxes_cxcywh = boxes_cxcywh[allowed_mask]
        max_scores = max_scores[allowed_mask]
        class_ids = class_ids[allowed_mask]

        thresholds = np.array(
            [max(conf_threshold, self._threshold_for_class_id(int(class_id))) for class_id in class_ids],
            dtype=np.float32,
        )
        conf_mask = max_scores >= thresholds
        if not conf_mask.any():
            return []

        boxes_cxcywh = boxes_cxcywh[conf_mask]
        max_scores = max_scores[conf_mask]
        class_ids = class_ids[conf_mask]

        cx, cy, w, h = (
            boxes_cxcywh[:, 0],
            boxes_cxcywh[:, 1],
            boxes_cxcywh[:, 2],
            boxes_cxcywh[:, 3],
        )
        x1 = cx - w / 2
        y1 = cy - h / 2
        x2 = cx + w / 2
        y2 = cy + h / 2
        boxes_xyxy = np.stack([x1, y1, x2, y2], axis=1)

        keep = _classwise_nms_numpy(boxes_xyxy, max_scores, class_ids, nms_iou)
        boxes_xyxy = boxes_xyxy[keep]
        max_scores = max_scores[keep]
        class_ids = class_ids[keep]

        scale = meta["scale"]
        pad_x = meta["pad_x"]
        pad_y = meta["pad_y"]

        boxes_xyxy[:, 0] = (boxes_xyxy[:, 0] - pad_x) / scale
        boxes_xyxy[:, 1] = (boxes_xyxy[:, 1] - pad_y) / scale
        boxes_xyxy[:, 2] = (boxes_xyxy[:, 2] - pad_x) / scale
        boxes_xyxy[:, 3] = (boxes_xyxy[:, 3] - pad_y) / scale

        boxes_xyxy[:, 0] = np.clip(boxes_xyxy[:, 0], 0, meta["orig_w"])
        boxes_xyxy[:, 1] = np.clip(boxes_xyxy[:, 1], 0, meta["orig_h"])
        boxes_xyxy[:, 2] = np.clip(boxes_xyxy[:, 2], 0, meta["orig_w"])
        boxes_xyxy[:, 3] = np.clip(boxes_xyxy[:, 3], 0, meta["orig_h"])

        detections: list[dict] = []
        for i in range(len(boxes_xyxy)):
            x1_f, y1_f, x2_f, y2_f = boxes_xyxy[i]
            class_id = int(class_ids[i])
            class_name = (
                self._class_names[class_id]
                if class_id < len(self._class_names)
                else f"class_{class_id}"
            )
            detections.append(
                {
                    "class_id": class_id,
                    "class_label": class_name,
                    "confidence": round(float(max_scores[i]), 4),
                    "bbox": [
                        float(x1_f),
                        float(y1_f),
                        float(x2_f - x1_f),
                        float(y2_f - y1_f),
                    ],
                    "bbox_xyxy": [float(x1_f), float(y1_f), float(x2_f), float(y2_f)],
                }
            )

        detections.sort(
            key=lambda det: (
                -float(det["confidence"]),
                int(det["class_id"]),
                float(det["bbox"][0]),
                float(det["bbox"][1]),
            )
        )
        return detections

    def detect(self, frame: np.ndarray) -> list[dict]:
        t_start = time.perf_counter()

        t_pre = time.perf_counter()
        blob, meta = self._preprocess(frame)
        self._last_preprocess_ms = (time.perf_counter() - t_pre) * 1000

        t_infer = time.perf_counter()
        result = self._compiled_model({self._input_layer: blob})
        output = result[self._output_layer]
        self._last_inference_ms = (time.perf_counter() - t_infer) * 1000

        t_post = time.perf_counter()
        detections = self._postprocess(output, meta)
        self._last_postprocess_ms = (time.perf_counter() - t_post) * 1000

        self._total_inferences += 1
        total_ms = (time.perf_counter() - t_start) * 1000
        log_fn = logger.info if self._total_inferences <= 5 else logger.debug
        log_fn(
            "Detection: %s objects in %.1fms (pre=%.1f infer=%.1f post=%.1f) [format=%s]",
            len(detections),
            total_ms,
            self._last_preprocess_ms,
            self._last_inference_ms,
            self._last_postprocess_ms,
            getattr(self, "_model_format", "unknown"),
        )
        return detections

    def detect_batch(self, frames: list[np.ndarray]) -> list[list[dict]]:
        if not frames:
            return []
        if len(frames) == 1:
            return [self.detect(frames[0])]

        t_start = time.perf_counter()
        t_pre = time.perf_counter()
        batch_blob, metas = self._preprocess_batch(frames)
        self._last_preprocess_ms = (time.perf_counter() - t_pre) * 1000

        all_detections: list[list[dict]] = []
        t_infer = time.perf_counter()

        input_shape = list(self._input_layer.shape)
        is_static_batch = input_shape[0] > 0 and input_shape[0] == 1
        if is_static_batch:
            for i in range(len(frames)):
                single_blob = batch_blob[i : i + 1]
                result = self._compiled_model({self._input_layer: single_blob})
                output = result[self._output_layer]
                all_detections.append(self._postprocess(output, metas[i]))
        else:
            result = self._compiled_model({self._input_layer: batch_blob})
            output = result[self._output_layer]
            for i in range(len(frames)):
                all_detections.append(self._postprocess(output[i : i + 1], metas[i]))

        self._last_inference_ms = (time.perf_counter() - t_infer) * 1000
        self._total_inferences += 1
        total_ms = (time.perf_counter() - t_start) * 1000
        logger.debug(
            "Batch detection: %s objects across %s frames in %.1fms",
            sum(len(d) for d in all_detections),
            len(frames),
            total_ms,
        )
        return all_detections

    def _resolve_precision(self) -> str:
        if self._precision_setting:
            return self._precision_setting
        model_name = Path(self._model_path).name.lower()
        if "_int8" in model_name:
            return "INT8"
        if "_fp16" in model_name:
            return "FP16"
        if "_fp32" in model_name:
            return "FP32"
        return "unknown"

    def get_model_info(self) -> dict:
        return {
            "model": self._resolved_model_path or self._model_path,
            "model_config_value": self._model_path,
            "device_requested": self._device,
            "device_actual": self._actual_device,
            "precision": self._resolve_precision(),
            "confidence_threshold": self._conf_threshold,
            "nms_iou_threshold": self._nms_iou,
            "input_size": [self._input_h, self._input_w],
            "classes": list(self._class_names),
            "allowed_classes": sorted(self._allowed_class_names),
            "allowed_class_ids": sorted(self._allowed_class_ids),
            "per_class_thresholds": dict(sorted(self._per_class_threshold.items())),
            "total_inferences": self._total_inferences,
            "last_inference_ms": round(self._last_inference_ms, 2),
            "last_preprocess_ms": round(self._last_preprocess_ms, 2),
            "last_postprocess_ms": round(self._last_postprocess_ms, 2),
        }

    def get_timing(self) -> dict:
        return {
            "inference_ms": self._last_inference_ms,
            "preprocess_ms": self._last_preprocess_ms,
            "postprocess_ms": self._last_postprocess_ms,
            "total_ms": self._last_preprocess_ms
            + self._last_inference_ms
            + self._last_postprocess_ms,
        }

    def shutdown(self) -> None:
        logger.info("Shutting down OpenVINO detector")
        self._compiled_model = None


detector = OpenVINODetector()
