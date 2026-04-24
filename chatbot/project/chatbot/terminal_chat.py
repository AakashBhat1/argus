from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from project.chatbot.query_engine import filter_events, normalize_query, parse_query, summarize_events
from project.embedding.embedding_model import embed_query
from project.intent.intent_detector import detect_intent
from project.llm.ollama_client import generate_answer_with_ollama, warm_ollama_model
from project.settings import DOCUMENTS_PATH, FAISS_INDEX_PATH, INDEX_RELOAD_SECONDS, TOP_K
from project.vector.vector_store import initialize_faiss_index, retrieve_similar_documents


logger = logging.getLogger(__name__)
NO_MATCH_MESSAGE = "No activity matching the request was found."
MAX_EVENTS_FOR_PROMPT = 50


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
        "latest",
        "recent",
        "zone",
    )
    if len(tokens) < 3 and not any(signal in q for signal in activity_signals):
        return True

    return tokens[-1] in {"every", "after", "before", "between", "from", "to", "at", "and", "camera", "zone"}


def _build_prompt(events_text: str, query: str, summary_text: str) -> str:
    return (
        "You are an AI surveillance monitoring assistant.\n"
        "Your job is to analyze surveillance events and answer questions about activity detected by cameras.\n\n"

        "IMPORTANT:\n"
        "You must base your answer ONLY on the events provided below.\n"
        "Do NOT invent or assume events that are not listed.\n\n"

        "Surveillance Events:\n"
        f"{events_text}\n\n"

"Event Summary:\n"
f"{summary_text}\n\n"

"User Question:\n"
f"{query}\n\n"

"Instructions:\n"
"1. Review the surveillance events carefully.\n"
"2. Identify important signals such as movement, intrusion, objects detected, cameras, and timestamps.\n"
"3. Determine whether the events answer the user's question.\n"
"4. If relevant activity exists, summarize the activity clearly.\n"
"5. If no events match the request, say exactly: No activity matching the request was found.\n\n"

"Response Rules:\n"
"- Use ONLY the information from the provided events.\n"
"- Do NOT hallucinate cameras, timestamps, objects, or intrusions.\n"
"- If the Event Summary shows matched events > 0, you must describe the activity.\n"
"- Focus on key details such as time, camera location, and event type.\n"
"- Keep the final response concise.\n"
"- Respond in 1–2 sentences.\n\n"

"Answer:"
    )


def _collect_events(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        grouped_events = document.get("events")
        if isinstance(grouped_events, list):
            events.extend(event for event in grouped_events if isinstance(event, dict))
            continue
        event = document.get("event")
        if isinstance(event, dict):
            events.append(event)
    return events


def _retrieve_events_via_faiss(
    query: str,
    index: Any,
    documents: list[dict[str, Any]],
    embeddings: Any,
    top_k: int,
) -> list[dict[str, Any]]:
    query_vector = embed_query(query)
    results = retrieve_similar_documents(index, documents, query_vector, top_k=top_k)
    events: list[dict[str, Any]] = []
    for document, _score in results:
        grouped_events = document.get("events")
        if isinstance(grouped_events, list):
            events.extend(event for event in grouped_events if isinstance(event, dict))
            continue
        event = document.get("event")
        if isinstance(event, dict):
            events.append(event)
    return events


def _format_events(events: list[dict[str, Any]]) -> str:
    if not events:
        return "No events."

    parts: list[str] = []
    for rank, event in enumerate(events, start=1):
        timestamp = str(event.get("timestamp", "unknown"))
        camera_id = str(event.get("camera_id", "unknown"))

        classes_value = event.get("classes")
        if isinstance(classes_value, dict):
            classes = [str(k).strip().lower() for k, v in classes_value.items() if str(k).strip() and v]
        elif isinstance(classes_value, list):
            classes = [str(value).strip().lower() for value in classes_value if str(value).strip()]
        else:
            classes = []
        if not classes:
            label = str(event.get("class_label", "unknown")).strip().lower()
            classes = [label] if label else ["unknown"]
        classes_text = ", ".join(classes)

        has_movement = bool(event.get("has_movement", True))
        has_intrusion = bool(event.get("has_intrusion", event.get("intrusion", False)))

        parts.append(
            (
                f"Event {rank}\n"
                f"Timestamp: {timestamp}\n"
                f"Camera: {camera_id}\n"
                f"Classes: {classes_text}\n"
                f"Movement: {str(has_movement).lower()}\n"
                f"Intrusion: {str(has_intrusion).lower()}"
            )
        )
    return "\n\n".join(parts)


def _format_summary(counts: dict[str, int], total_events: int) -> str:
    if not counts:
        return f"Total matched events: {total_events}\nObject counts: none"
    counts_text = ", ".join(f"{label}={value}" for label, value in sorted(counts.items()))
    return f"Total matched events: {total_events}\nObject counts: {counts_text}"


def _is_camera_count_query(query: str) -> bool:
    q = (query or "").strip().lower()
    patterns = (
        r"\bhow many\b.*\bcameras?\b",
        r"\bnumber of\b.*\bcameras?\b",
        r"\bcamera count\b",
        r"\bcount\b.*\bcameras?\b",
    )
    return any(re.search(pattern, q) for pattern in patterns)


def _camera_count_answer(events: list[dict[str, Any]]) -> str:
    camera_ids = {
        str(event.get("camera_id")).strip()
        for event in events
        if str(event.get("camera_id", "")).strip() and str(event.get("camera_id")).strip().lower() != "unknown"
    }
    if not camera_ids:
        return "I could not determine any camera IDs from the available activity logs."
    return f"I found {len(camera_ids)} cameras in the available activity logs."


def _deterministic_answer(query: str, counts: dict[str, int], total_events: int) -> str:
    if total_events <= 0:
        return NO_MATCH_MESSAGE

    lowered_query = (query or "").strip().lower()
    if "how many" in lowered_query or "count" in lowered_query:
        if counts:
            count_text = ", ".join(f"{name}: {value}" for name, value in sorted(counts.items()))
            return f"I found {total_events} matching events. Counts are {count_text}."
        return f"I found {total_events} matching events."

    if counts:
        count_text = ", ".join(f"{name}: {value}" for name, value in sorted(counts.items()))
        return f"I found {total_events} matching events. Objects detected: {count_text}."
    return f"I found {total_events} matching events."


def _reply_for_intent(intent: str) -> str | None:
    if intent == "greeting":
        return (
            "Hello! I can help you check surveillance activity.\n"
            "You can ask about intrusions, people detected, or camera events."
        )
    if intent == "help":
        return (
            "You can ask things like:\n"
            "* Was a person detected in the ROI?\n"
            "* Any intrusion events today?\n"
            "* What happened last night?\n"
            "* Were any cars detected?"
        )
    return None


def run_terminal_chatbot() -> None:
    logger.info("Loading FAISS index and documents...")
    index, documents = initialize_faiss_index()
    embeddings = None
    logger.info(
        "Vector store state at startup: index=%s docs=%s",
        0 if index is None else int(index.ntotal),
        len(documents),
    )

    last_index_mtime = Path(FAISS_INDEX_PATH).stat().st_mtime if Path(FAISS_INDEX_PATH).exists() else 0.0
    last_docs_mtime = Path(DOCUMENTS_PATH).stat().st_mtime if Path(DOCUMENTS_PATH).exists() else 0.0
    last_reload_time = time.time()

    logger.info("Warming Ollama model...")
    warm_ollama_model()
    print("Security Chatbot ready. Type 'exit' to quit.\n")

    while True:
        query = input("User> ").strip()
        if query.lower() in {"exit", "quit"}:
            print("Assistant> Shutting down.")
            break
        if not query:
            print("Assistant> Please enter a question.")
            continue

        parsed_filters = parse_query(query)
        intent = detect_intent(query)
        intent_reply = _reply_for_intent(intent)
        if intent_reply is not None:
            print(f"Assistant> {intent_reply}\n")
            continue
        if _is_incomplete_activity_query(query):
            print("Assistant> Could you please clarify your question?\n")
            continue

        now = time.time()
        if now - last_reload_time >= INDEX_RELOAD_SECONDS:
            index_exists = Path(FAISS_INDEX_PATH).exists()
            docs_exists = Path(DOCUMENTS_PATH).exists()
            current_index_mtime = Path(FAISS_INDEX_PATH).stat().st_mtime if index_exists else 0.0
            current_docs_mtime = Path(DOCUMENTS_PATH).stat().st_mtime if docs_exists else 0.0

            should_reload = (
                current_index_mtime != last_index_mtime
                or current_docs_mtime != last_docs_mtime
            )
            if (index is None or not documents) and (index_exists or docs_exists):
                should_reload = True

            if should_reload:
                index, documents = initialize_faiss_index()
                last_index_mtime = current_index_mtime
                last_docs_mtime = current_docs_mtime
                logger.info(
                    "Reloaded vector store: index=%s docs=%s",
                    0 if index is None else int(index.ntotal),
                    len(documents),
                )
            last_reload_time = now

        if index is None or not documents:
            print(f"Assistant> {NO_MATCH_MESSAGE}\n")
            continue

        all_events = _collect_events(documents)
        if not all_events:
            print(f"Assistant> {NO_MATCH_MESSAGE}\n")
            continue
        if _is_camera_count_query(query):
            print(f"Assistant> {_camera_count_answer(all_events)}\n")
            continue

        normalized_query = normalize_query(query)
        matched_events: list[dict[str, Any]] = []

        has_date_filter = parsed_filters.get("date_from") is not None or parsed_filters.get("date_to") is not None
        if bool(parsed_filters.get("has_structured_filters")):
            matched_events = filter_events(all_events, parsed_filters)
            if not matched_events and not has_date_filter:
                matched_events = _retrieve_events_via_faiss(normalized_query, index, documents, embeddings, top_k=TOP_K)
        else:
            matched_events = _retrieve_events_via_faiss(normalized_query, index, documents, embeddings, top_k=TOP_K)

        if not matched_events:
            print(f"Assistant> {NO_MATCH_MESSAGE}\n")
            continue

        event_counts = summarize_events(matched_events)
        prompt = _build_prompt(
            _format_events(matched_events[:MAX_EVENTS_FOR_PROMPT]),
            query,
            _format_summary(event_counts, len(matched_events)),
        )
        answer = generate_answer_with_ollama(prompt)
        if not answer.strip():
            answer = _deterministic_answer(query, event_counts, len(matched_events))
        elif len(matched_events) > 0 and NO_MATCH_MESSAGE.lower() in answer.lower():
            answer = _deterministic_answer(query, event_counts, len(matched_events))
        print(f"Assistant> {answer}\n")
