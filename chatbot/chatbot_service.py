from __future__ import annotations

import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from project.chatbot.query_engine import filter_events, parse_query, summarize_events
from project.embedding.embedding_model import embed_query, initialize_embedding_model
from project.ingestion.ingestion_worker import run_ingestion_worker
from project.intent.intent_detector import detect_intent, initialize_intent_detector
from project.llm.ollama_client import generate_answer_with_ollama, warm_ollama_model
from project.settings import DOCUMENTS_PATH, FAISS_INDEX_PATH, INDEX_RELOAD_SECONDS, TOP_K
from project.vector.vector_store import initialize_faiss_index, retrieve_similar_documents


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)

NO_MATCH_MESSAGE = "No activity matching the request was found."
MAX_EVENTS_FOR_PROMPT = 50


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(default=TOP_K, ge=1, le=50)


class ChatResponse(BaseModel):
    reply: str
    incidents: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None


class RetrievalState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.index, self.documents = initialize_faiss_index()
        self.last_index_mtime = Path(FAISS_INDEX_PATH).stat().st_mtime if Path(FAISS_INDEX_PATH).exists() else 0.0
        self.last_docs_mtime = Path(DOCUMENTS_PATH).stat().st_mtime if Path(DOCUMENTS_PATH).exists() else 0.0
        self.last_reload_time = time.time()

    def maybe_reload(self) -> None:
        now = time.time()
        if (now - self.last_reload_time) < INDEX_RELOAD_SECONDS:
            return

        index_exists = Path(FAISS_INDEX_PATH).exists()
        docs_exists = Path(DOCUMENTS_PATH).exists()
        current_index_mtime = Path(FAISS_INDEX_PATH).stat().st_mtime if index_exists else 0.0
        current_docs_mtime = Path(DOCUMENTS_PATH).stat().st_mtime if docs_exists else 0.0
        should_reload = (
            current_index_mtime != self.last_index_mtime
            or current_docs_mtime != self.last_docs_mtime
            or ((self.index is None or not self.documents) and (index_exists or docs_exists))
        )

        if should_reload:
            self.index, self.documents = initialize_faiss_index()
            self.last_index_mtime = current_index_mtime
            self.last_docs_mtime = current_docs_mtime
            logger.info(
                "Reloaded vector store: index=%s docs=%s",
                0 if self.index is None else int(self.index.ntotal),
                len(self.documents),
            )
        self.last_reload_time = now


STATE = RetrievalState()
STOP_EVENT = threading.Event()
WORKER_THREAD: threading.Thread | None = None


def normalize_query(query: str) -> str:
    q = (query or "").lower().strip()

    def _replace_time(match: re.Match[str]) -> str:
        hour = int(match.group(1))
        minute = match.group(2)
        meridiem = match.group(3)
        if minute is None:
            return f"{hour}:00 {meridiem}"
        return f"{hour}:{minute} {meridiem}"

    q = re.sub(r"\b(\d{1,2})(?:\s*:\s*(\d{2}))?\s*(am|pm)\b", _replace_time, q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _is_incomplete_activity_query(query: str) -> bool:
    q = (query or "").strip().lower()
    tokens = re.findall(r"[a-z0-9:]+", q)
    if not tokens:
        return True

    activity_signals = (
        "roi",
        "intrusion",
        "intruder",
        "intrution",
        "movement",
        "motion",
        "person",
        "people",
        "car",
        "cars",
        "bike",
        "bikes",
        "bus",
        "buses",
        "camera",
        "suspicious",
        "activity",
        "today",
        "night",
        "count",
        "how many",
    )
    if len(tokens) < 3 and not any(signal in q for signal in activity_signals):
        return True

    return tokens[-1] in {"every", "after", "before", "between", "from", "to", "at", "and"}


def _reply_for_intent(intent: str) -> str | None:
    if intent == "greeting":
        return "Hello. Ask about intrusions, movement windows, camera activity, or object counts."
    if intent == "help":
        return (
            "Try: 'Did anyone enter the restricted area today?', "
            "'movement between 1am and 2am', or 'how many intrusions near entrance camera?'."
        )
    return None


def _event_classes(event: dict[str, Any]) -> list[str]:
    classes = event.get("classes")
    if isinstance(classes, list):
        values = [str(item).strip().lower() for item in classes if str(item).strip()]
        if values:
            return values
    class_label = str(event.get("class_label", "")).strip().lower()
    return [class_label] if class_label else []


def _collect_events(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        event = document.get("event")
        if isinstance(event, dict):
            events.append(event)
    return events


def _retrieve_events_via_faiss(query: str, top_k: int) -> list[dict[str, Any]]:
    if STATE.index is None or not STATE.documents:
        return []
    query_vector = embed_query(query)
    results = retrieve_similar_documents(STATE.index, STATE.documents, query_vector, top_k=top_k)
    events: list[dict[str, Any]] = []
    for document, _ in results:
        event = document.get("event")
        if isinstance(event, dict):
            events.append(event)
    return events


def _format_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return "No events."

    lines: list[str] = []
    for idx, event in enumerate(events, start=1):
        classes = ", ".join(_event_classes(event)) or "unknown"
        lines.append(
            (
                f"Event {idx}\n"
                f"Timestamp: {event.get('timestamp', 'unknown')}\n"
                f"Camera: {event.get('camera_id', 'unknown')}\n"
                f"Classes: {classes}\n"
                f"Intrusion: {str(bool(event.get('intrusion', False))).lower()}"
            )
        )
    return "\n\n".join(lines)


def _format_summary(counts: dict[str, int], total_events: int) -> str:
    if not counts:
        return f"Total matched events: {total_events}\nObject counts: none"
    counts_text = ", ".join(f"{label}={value}" for label, value in sorted(counts.items()))
    return f"Total matched events: {total_events}\nObject counts: {counts_text}"


def _build_prompt(events_text: str, query: str, summary_text: str) -> str:
    return (
        "You are a surveillance monitoring assistant.\n\n"
        "Use the events below to answer the user's question.\n\n"
        f"Events:\n{events_text}\n\n"
        f"Event Summary:\n{summary_text}\n\n"
        f"User question:\n{query}\n\n"
        "Rules:\n"
        "- Use ONLY the events provided.\n"
        "- Do not invent events.\n"
        "- If summary says events > 0, do not say no activity.\n"
        "- If there are no events, say: No activity matching the request was found.\n"
        "- Respond in 1-2 sentences.\n"
    )


def _deterministic_answer(query: str, counts: dict[str, int], total_events: int) -> str:
    if total_events <= 0:
        return NO_MATCH_MESSAGE
    lowered = (query or "").lower()
    if "how many" in lowered or "count" in lowered:
        if counts:
            details = ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))
            return f"I found {total_events} matching events. Counts are {details}."
        return f"I found {total_events} matching events."
    if counts:
        details = ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))
        return f"I found {total_events} matching events. Objects detected: {details}."
    return f"I found {total_events} matching events."


def _event_zone(event: dict[str, Any]) -> str | None:
    names = event.get("roi_zone_names")
    if isinstance(names, list) and names:
        text = ", ".join(str(item) for item in names if str(item).strip()).strip()
        if text:
            return text
    zone_ids = event.get("roi_zone_ids")
    if isinstance(zone_ids, list) and zone_ids:
        text = ", ".join(str(item) for item in zone_ids if str(item).strip()).strip()
        if text:
            return text
    return None


def _incidents(events: list[dict[str, Any]], max_items: int = 25) -> list[dict[str, Any]]:
    incidents: list[dict[str, Any]] = []
    for event in events[:max_items]:
        event_type = "intrusion" if bool(event.get("intrusion", False)) else "movement"
        incidents.append(
            {
                "camera_id": str(event.get("camera_id", "unknown")),
                "timestamp": str(event.get("timestamp", "unknown")),
                "zone_name": _event_zone(event),
                "type": event_type,
            }
        )
    return incidents


def answer_message(message: str, top_k: int) -> ChatResponse:
    query = message.strip()
    intent = detect_intent(query)
    intent_reply = _reply_for_intent(intent)
    if intent_reply is not None:
        return ChatResponse(reply=intent_reply, incidents=[], metadata={"intent": intent, "source": "intent"})

    if _is_incomplete_activity_query(query):
        return ChatResponse(
            reply="Could you clarify your question with camera, event type, or time range?",
            incidents=[],
            metadata={"intent": "activity_query", "source": "validator"},
        )

    with STATE.lock:
        STATE.maybe_reload()
        if STATE.index is None or not STATE.documents:
            return ChatResponse(reply=NO_MATCH_MESSAGE, incidents=[], metadata={"source": "vector_store"})

        parsed_filters = parse_query(query)
        all_events = _collect_events(STATE.documents)
        if not all_events:
            return ChatResponse(reply=NO_MATCH_MESSAGE, incidents=[], metadata={"source": "documents"})

        normalized_query = normalize_query(query)
        if bool(parsed_filters.get("has_structured_filters")):
            matched_events = filter_events(all_events, parsed_filters)
        else:
            matched_events = _retrieve_events_via_faiss(normalized_query, top_k=top_k)

        if not matched_events:
            return ChatResponse(reply=NO_MATCH_MESSAGE, incidents=[], metadata={"source": "retriever"})

        counts = summarize_events(matched_events)
        prompt = _build_prompt(
            _format_events(matched_events[:MAX_EVENTS_FOR_PROMPT]),
            query,
            _format_summary(counts, len(matched_events)),
        )
        answer = generate_answer_with_ollama(prompt).strip()
        if (
            (not answer)
            or answer.lower().startswith("ollama request failed")
            or (NO_MATCH_MESSAGE.lower() in answer.lower() and len(matched_events) > 0)
        ):
            answer = _deterministic_answer(query, counts, len(matched_events))

        return ChatResponse(
            reply=answer,
            incidents=_incidents(matched_events),
            metadata={
                "intent": "activity_query",
                "source": "roi_events+faiss",
                "matched_events": len(matched_events),
                "filters": parsed_filters,
            },
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global WORKER_THREAD

    initialize_embedding_model()
    initialize_intent_detector()
    warm_ollama_model()

    STOP_EVENT.clear()
    WORKER_THREAD = threading.Thread(target=run_ingestion_worker, args=(STOP_EVENT,), daemon=True, name="ingestion-worker")
    WORKER_THREAD.start()
    logger.info("Chatbot API started.")
    try:
        yield
    finally:
        STOP_EVENT.set()
        if WORKER_THREAD is not None:
            WORKER_THREAD.join(timeout=5)
        logger.info("Chatbot API stopped.")


app = FastAPI(title="Surveillance Chatbot API", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "healthy"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    return answer_message(request.message, request.top_k)
