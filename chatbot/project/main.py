from __future__ import annotations

import logging
import threading

from project.chatbot.terminal_chat import run_terminal_chatbot
from project.embedding.embedding_model import initialize_embedding_model
from project.ingestion.ingestion_worker import run_ingestion_worker
from project.intent.intent_detector import initialize_intent_detector


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.WARNING)

    # Preload embedding model once at startup; reused by ingestion and chatbot threads.
    initialize_embedding_model()
    # Load intent phrases once at startup.
    initialize_intent_detector()

    stop_event = threading.Event()
    worker_thread = threading.Thread(
        target=run_ingestion_worker,
        args=(stop_event,),
        daemon=True,
        name="ingestion-worker",
    )
    worker_thread.start()

    try:
        run_terminal_chatbot()
    finally:
        stop_event.set()
        worker_thread.join(timeout=5)


if __name__ == "__main__":
    main()
