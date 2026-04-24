from __future__ import annotations

import os
import pickle
import tempfile
import threading
import logging
from pathlib import Path
from typing import Any

import faiss
import numpy as np

from project.settings import DOCUMENTS_PATH, FAISS_INDEX_PATH


_VECTOR_STORE_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


def _fsync_parent(path: Path) -> None:
    if os.name != "posix":
        return
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _cleanup_temp(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _atomic_write_pickle(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent) as tmp:
            pickle.dump(payload, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            temp_name = tmp.name
        os.replace(temp_name, path)
        _fsync_parent(path)
    except Exception:
        if temp_name:
            _cleanup_temp(temp_name)
        raise


def _atomic_write_faiss(index: faiss.Index, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent) as tmp:
            temp_name = tmp.name
        faiss.write_index(index, temp_name)
        with open(temp_name, "rb+") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        _fsync_parent(path)
    except Exception:
        if temp_name:
            _cleanup_temp(temp_name)
        raise


def initialize_faiss_index(
    index_path: str | Path = FAISS_INDEX_PATH,
    documents_path: str | Path = DOCUMENTS_PATH,
) -> tuple[faiss.Index | None, list[dict[str, Any]]]:
    with _VECTOR_STORE_LOCK:
        index_file = Path(index_path)
        documents_file = Path(documents_path)

        index: faiss.Index | None = None
        documents: list[dict[str, Any]] = []

        if documents_file.exists():
            try:
                with documents_file.open("rb") as handle:
                    loaded = pickle.load(handle)
                    if isinstance(loaded, list):
                        documents = loaded
            except Exception as exc:  # pragma: no cover - startup resilience
                logger.warning("Failed to read documents file %s: %s", documents_file, exc)
                documents = []

        if index_file.exists():
            try:
                index = faiss.read_index(str(index_file))
            except Exception as exc:  # pragma: no cover - startup resilience
                logger.warning("Failed to read FAISS index file %s: %s", index_file, exc)
                index = None

        if index is None and documents:
            logger.warning(
                "Documents exist (%s) but FAISS index is unavailable. Returning empty state for replay.",
                len(documents),
            )
            return None, []

        if index is not None and index.ntotal != len(documents):
            logger.warning(
                "FAISS/documents mismatch detected (index=%s docs=%s). Returning empty state for replay.",
                int(index.ntotal),
                len(documents),
            )
            index = None
            documents = []

        return index, documents


def persist_vector_store(
    index: faiss.Index | None,
    documents: list[dict[str, Any]],
    index_path: str | Path = FAISS_INDEX_PATH,
    documents_path: str | Path = DOCUMENTS_PATH,
) -> None:
    if index is None:
        return

    with _VECTOR_STORE_LOCK:
        _atomic_write_faiss(index, Path(index_path))
        _atomic_write_pickle(Path(documents_path), list(documents))


def append_vectors(
    index: faiss.Index | None,
    existing_documents: list[dict[str, Any]],
    new_vectors: np.ndarray,
    new_documents: list[dict[str, Any]],
    index_path: str | Path = FAISS_INDEX_PATH,
    documents_path: str | Path = DOCUMENTS_PATH,
    persist: bool = True,
) -> tuple[faiss.Index, list[dict[str, Any]]]:
    if new_vectors.ndim != 2:
        raise ValueError("new_vectors must be 2D")
    if new_vectors.shape[0] != len(new_documents):
        raise ValueError("new_vectors row count must match new_documents length")
    if new_vectors.shape[0] == 0:
        if index is None:
            raise ValueError("Cannot initialize empty index without vectors")
        return index, existing_documents

    vectors = np.ascontiguousarray(new_vectors, dtype=np.float32)
    with _VECTOR_STORE_LOCK:
        if index is None and existing_documents:
            raise ValueError("Inconsistent vector state: index is None while documents already exist")
        if index is None:
            index = faiss.IndexFlatIP(int(vectors.shape[1]))

        index.add(vectors)
        updated_documents = list(existing_documents) + list(new_documents)

        if persist:
            persist_vector_store(index, updated_documents, index_path, documents_path)
        return index, updated_documents


def retrieve_similar_documents(
    index: faiss.Index | None,
    documents: list[dict[str, Any]],
    query_vector: np.ndarray,
    top_k: int = 5,
) -> list[tuple[dict[str, Any], float]]:
    if index is None or index.ntotal == 0 or not documents:
        return []

    query = np.ascontiguousarray(query_vector.reshape(1, -1), dtype=np.float32)
    scores, indices = index.search(query, min(top_k, len(documents)))

    results: list[tuple[dict[str, Any], float]] = []
    for score, doc_index in zip(scores[0], indices[0]):
        if doc_index < 0 or doc_index >= len(documents):
            continue
        results.append((documents[doc_index], float(score)))
    return results
