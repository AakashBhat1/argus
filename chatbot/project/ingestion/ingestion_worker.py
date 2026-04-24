from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import faiss

from project.embedding.embedding_model import embed_documents
from project.ingestion.db_loader import (
    default_db_cursor,
    load_events_from_db,
    read_db_checkpoint,
    write_db_checkpoint,
)
from project.ingestion.document_builder import build_documents_from_events
from project.ingestion.event_loader import load_events_from_jsonl, read_checkpoint, write_checkpoint
from project.settings import (
    CHATBOT_DATABASE_URL,
    CHATBOT_TENANT_ID,
    CHECKPOINT_PATH,
    DOCUMENTS_PATH,
    FAISS_INDEX_PATH,
    INGESTION_SOURCE,
    INGESTION_INTERVAL_SECONDS,
    MAX_LINES_PER_BATCH,
    POSTGRES_CHECKPOINT_PATH,
    ROI_EVENTS_PATH,
    VECTOR_FLUSH_DOC_THRESHOLD,
    VECTOR_FLUSH_INTERVAL_SECONDS,
)
from project.vector.vector_store import append_vectors, initialize_faiss_index, persist_vector_store


logger = logging.getLogger(__name__)


def _document_event_id(document: dict[str, Any]) -> str:
    raw_id = document.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()

    metadata = document.get("metadata")
    if isinstance(metadata, dict):
        metadata_id = metadata.get("event_id")
        if isinstance(metadata_id, str) and metadata_id.strip():
            return metadata_id.strip()

    event = document.get("event")
    if isinstance(event, dict):
        event_id = event.get("event_id")
        if isinstance(event_id, str) and event_id.strip():
            return event_id.strip()
    return ""


def _existing_event_ids(documents: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            continue
        event_id = _document_event_id(document)
        if event_id:
            ids.add(event_id)
    return ids


@dataclass
class IngestionState:
    index: faiss.Index | None
    documents: list[dict[str, Any]]
    processed_event_ids: set[str]
    source: str
    read_cursor: int | dict[str, str]
    persisted_cursor: int | dict[str, str]
    pending_docs: int = 0
    last_flush_monotonic: float = field(default_factory=time.monotonic)


def _normalize_source() -> str:
    source = (INGESTION_SOURCE or "").strip().lower()
    return source if source in {"postgres", "jsonl"} else "postgres"


def _clone_cursor(cursor: int | dict[str, str]) -> int | dict[str, str]:
    if isinstance(cursor, dict):
        return dict(cursor)
    return int(cursor)


def _cursor_changed(
    a: int | dict[str, str],
    b: int | dict[str, str],
) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a != b
    return int(a) != int(b)


def _cursor_has_progress(source: str, cursor: int | dict[str, str]) -> bool:
    if source == "postgres":
        if not isinstance(cursor, dict):
            return False
        return cursor != default_db_cursor()
    return int(cursor) > 0


def _load_ingestion_state() -> IngestionState:
    index, documents = initialize_faiss_index(FAISS_INDEX_PATH, DOCUMENTS_PATH)
    source = _normalize_source()
    if source == "postgres":
        checkpoint_cursor: int | dict[str, str] = read_db_checkpoint(POSTGRES_CHECKPOINT_PATH)
        if not CHATBOT_DATABASE_URL:
            logger.error("INGESTION_SOURCE=postgres requires CHATBOT_DATABASE_URL to be configured.")
    else:
        checkpoint_cursor = read_checkpoint(CHECKPOINT_PATH, ROI_EVENTS_PATH)

    # If persisted vectors are unavailable, replay from the beginning for a safe rebuild.
    if (index is None or not documents) and _cursor_has_progress(source, checkpoint_cursor):
        logger.info(
            "Vector store is unavailable while checkpoint=%s. Resetting checkpoint for replay.",
            checkpoint_cursor,
        )
        if source == "postgres":
            write_db_checkpoint(default_db_cursor(), POSTGRES_CHECKPOINT_PATH)
            checkpoint_cursor = default_db_cursor()
        else:
            write_checkpoint(0, CHECKPOINT_PATH)
            checkpoint_cursor = 0
        if index is None:
            documents = []

    return IngestionState(
        index=index,
        documents=documents,
        processed_event_ids=_existing_event_ids(documents),
        source=source,
        read_cursor=_clone_cursor(checkpoint_cursor),
        persisted_cursor=_clone_cursor(checkpoint_cursor),
    )


def _should_flush(state: IngestionState) -> bool:
    if state.pending_docs <= 0:
        return False
    if state.pending_docs >= VECTOR_FLUSH_DOC_THRESHOLD:
        return True
    return (time.monotonic() - state.last_flush_monotonic) >= VECTOR_FLUSH_INTERVAL_SECONDS


def _flush_state(state: IngestionState) -> bool:
    flushed = False

    if state.index is not None and state.pending_docs > 0:
        persist_vector_store(
            state.index,
            state.documents,
            FAISS_INDEX_PATH,
            DOCUMENTS_PATH,
        )
        flushed = True

    if _cursor_changed(state.persisted_cursor, state.read_cursor):
        if state.source == "postgres":
            write_db_checkpoint(
                dict(state.read_cursor) if isinstance(state.read_cursor, dict) else default_db_cursor(),
                POSTGRES_CHECKPOINT_PATH,
            )
        else:
            write_checkpoint(int(state.read_cursor), CHECKPOINT_PATH)
        flushed = True

    if flushed:
        state.persisted_cursor = _clone_cursor(state.read_cursor)
        state.pending_docs = 0
        state.last_flush_monotonic = time.monotonic()

    return flushed


def process_ingestion_batch(state: IngestionState) -> dict[str, int]:
    if state.source == "postgres":
        if not CHATBOT_DATABASE_URL:
            raise RuntimeError("INGESTION_SOURCE=postgres but CHATBOT_DATABASE_URL is missing")
        events, next_cursor, processed_units = load_events_from_db(
            database_url=CHATBOT_DATABASE_URL,
            tenant_id=CHATBOT_TENANT_ID,
            checkpoint_path=POSTGRES_CHECKPOINT_PATH,
            max_rows=MAX_LINES_PER_BATCH,
            start_cursor=dict(state.read_cursor) if isinstance(state.read_cursor, dict) else default_db_cursor(),
        )
    else:
        events, next_cursor, processed_units = load_events_from_jsonl(
            jsonl_path=ROI_EVENTS_PATH,
            checkpoint_path=CHECKPOINT_PATH,
            max_lines=MAX_LINES_PER_BATCH,
            start_offset=int(state.read_cursor),
        )

    if processed_units == 0:
        flushed = _flush_state(state) if _should_flush(state) else False
        return {
            "processed_units": 0,
            "event_count": 0,
            "inserted_docs": 0,
            "duplicate_docs": 0,
            "flushed": int(flushed),
        }

    documents = build_documents_from_events(events)
    unique_documents: list[dict[str, Any]] = []
    duplicate_docs = 0
    batch_ids: set[str] = set()

    for document in documents:
        if not isinstance(document, dict):
            continue
        event_id = _document_event_id(document)
        if not event_id:
            continue
        if event_id in batch_ids or event_id in state.processed_event_ids:
            duplicate_docs += 1
            continue
        batch_ids.add(event_id)
        unique_documents.append(document)

    inserted_docs = 0
    if unique_documents:
        vectors = embed_documents([doc["text"] for doc in unique_documents])
        state.index, state.documents = append_vectors(
            state.index,
            state.documents,
            vectors,
            unique_documents,
            FAISS_INDEX_PATH,
            DOCUMENTS_PATH,
            persist=False,
        )
        state.processed_event_ids.update(batch_ids)
        inserted_docs = len(unique_documents)
        state.pending_docs += inserted_docs

    # Offset is advanced only after processing succeeds to avoid dropping data on failures.
    state.read_cursor = _clone_cursor(next_cursor)

    if inserted_docs == 0 and state.pending_docs == 0 and _cursor_changed(state.persisted_cursor, state.read_cursor):
        flushed = _flush_state(state)
    elif _should_flush(state):
        flushed = _flush_state(state)
    else:
        flushed = False

    return {
        "processed_units": processed_units,
        "event_count": len(events),
        "inserted_docs": inserted_docs,
        "duplicate_docs": duplicate_docs,
        "flushed": int(flushed),
    }


def run_ingestion_worker(stop_event: threading.Event) -> None:
    source = _normalize_source()
    logger.info(
        (
            "Ingestion worker started: source=%s tenant=%s interval=%ss "
            "max_batch=%s flush_docs=%s flush_interval=%ss"
        ),
        source,
        CHATBOT_TENANT_ID,
        INGESTION_INTERVAL_SECONDS,
        MAX_LINES_PER_BATCH,
        VECTOR_FLUSH_DOC_THRESHOLD,
        VECTOR_FLUSH_INTERVAL_SECONDS,
    )
    state = _load_ingestion_state()
    try:
        while not stop_event.is_set():
            try:
                stats = process_ingestion_batch(state)
                if stats["processed_units"] > 0 or stats["flushed"] > 0:
                    logger.info(
                        (
                            "Ingestion batch: source=%s units=%s events=%s inserted=%s "
                            "duplicates=%s pending=%s flushed=%s"
                        ),
                        state.source,
                        stats["processed_units"],
                        stats["event_count"],
                        stats["inserted_docs"],
                        stats["duplicate_docs"],
                        state.pending_docs,
                        bool(stats["flushed"]),
                    )
            except Exception as exc:  # pragma: no cover - runtime resilience
                logger.exception("Ingestion worker error: %s", exc)

            stop_event.wait(INGESTION_INTERVAL_SECONDS)
    finally:
        try:
            flushed = _flush_state(state)
            if flushed:
                logger.info("Flushed pending vectors/checkpoint during shutdown.")
        except Exception as exc:  # pragma: no cover - runtime resilience
            logger.exception("Failed to flush ingestion state at shutdown: %s", exc)
