from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np
from sentence_transformers.cross_encoder import CrossEncoder

from project.settings import (
    RERANKER_BATCH_SIZE,
    RERANKER_DEVICE,
    RERANKER_ENABLED,
    RERANKER_LOCAL_FILES_ONLY,
    RERANKER_MODEL_NAME,
)


logger = logging.getLogger(__name__)
_RERANKER_LOCK = threading.Lock()
_RERANKER_MODEL: CrossEncoder | None = None
_RERANKER_LOAD_FAILED = False


def initialize_reranker() -> CrossEncoder | None:
    global _RERANKER_MODEL, _RERANKER_LOAD_FAILED
    if not RERANKER_ENABLED or not RERANKER_MODEL_NAME:
        return None
    if _RERANKER_LOAD_FAILED:
        return None
    if _RERANKER_MODEL is None:
        with _RERANKER_LOCK:
            if _RERANKER_MODEL is None and not _RERANKER_LOAD_FAILED:
                try:
                    _RERANKER_MODEL = CrossEncoder(
                        RERANKER_MODEL_NAME,
                        device=RERANKER_DEVICE,
                        local_files_only=RERANKER_LOCAL_FILES_ONLY,
                    )
                except Exception as exc:  # pragma: no cover - runtime resilience
                    _RERANKER_LOAD_FAILED = True
                    logger.warning("Failed to load reranker model %s: %s", RERANKER_MODEL_NAME, exc)
                    return None
    return _RERANKER_MODEL


def rerank_documents(query: str, documents: list[dict[str, Any]]) -> list[float]:
    if not documents:
        return []

    model = initialize_reranker()
    if model is None:
        return [0.0 for _ in documents]

    pairs = [(query, str(document.get("text", "")).strip()) for document in documents]
    try:
        scores = model.predict(
            pairs,
            batch_size=RERANKER_BATCH_SIZE,
            show_progress_bar=False,
        )
    except Exception as exc:  # pragma: no cover - runtime resilience
        logger.warning("Reranker prediction failed: %s", exc)
        return [0.0 for _ in documents]

    return [float(score) for score in np.asarray(scores, dtype=np.float32)]
