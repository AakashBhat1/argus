from __future__ import annotations

import threading

import numpy as np
from sentence_transformers import SentenceTransformer

from project.settings import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DEVICE,
    EMBEDDING_LOCAL_FILES_ONLY,
    EMBEDDING_MODEL_NAME,
)


_MODEL_LOCK = threading.Lock()
_EMBEDDING_MODEL: SentenceTransformer | None = None


def initialize_embedding_model() -> SentenceTransformer:
    """
    Load embedding model once and keep it resident for the process lifetime.
    """
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        with _MODEL_LOCK:
            if _EMBEDDING_MODEL is None:
                try:
                    _EMBEDDING_MODEL = SentenceTransformer(
                        EMBEDDING_MODEL_NAME,
                        device=EMBEDDING_DEVICE,
                        local_files_only=EMBEDDING_LOCAL_FILES_ONLY,
                    )
                except OSError as exc:
                    raise RuntimeError(
                        "Failed to load local embedding model. "
                        "Ensure sentence-transformers/all-MiniLM-L6-v2 is cached locally "
                        "or set EMBEDDING_LOCAL_FILES_ONLY=0 for first-time download."
                    ) from exc
    return _EMBEDDING_MODEL


def _get_embedding_model() -> SentenceTransformer:
    model = _EMBEDDING_MODEL
    if model is None:
        return initialize_embedding_model()
    return model


def embed_documents(documents: list[str]) -> np.ndarray:
    """
    Embed a batch of documents using the persistent local model.
    """
    if not documents:
        return np.empty((0, 0), dtype=np.float32)

    model = _get_embedding_model()
    vectors = model.encode(
        documents,
        batch_size=EMBEDDING_BATCH_SIZE,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def embed_query(query: str) -> np.ndarray:
    """
    Embed one user query using the same persistent local model.
    """
    text = query.strip()
    if not text:
        raise ValueError("Query must not be empty")
    return embed_documents([text])[0]

