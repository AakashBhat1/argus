"""
Standalone ingestion script.

Reads all roi_events from Postgres, builds temporal-window documents,
embeds them, and saves the FAISS index + documents to disk.

Run once before starting the chatbot so the index is pre-built:

    python run_ingestion.py
"""
from __future__ import annotations

import os

# Must be set before any transformers / sentence_transformers import.
os.environ["USE_TF"] = "0"
os.environ["USE_TORCH"] = "1"

import logging
import time

from project.embedding.embedding_model import initialize_embedding_model
from project.ingestion.ingestion_worker import (
    _flush_state,
    _load_ingestion_state,
    process_ingestion_batch,
)
from project.settings import CHATBOT_DATABASE_URL, INGESTION_SOURCE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def main() -> None:
    source = (INGESTION_SOURCE or "").strip().lower() or "postgres"
    if source == "postgres" and not CHATBOT_DATABASE_URL:
        raise RuntimeError("INGESTION_SOURCE=postgres but CHATBOT_DATABASE_URL is missing")

    logger.info("Initializing embedding model...")
    initialize_embedding_model()

    logger.info("Loading ingestion state (existing index + checkpoint)...")
    state = _load_ingestion_state()

    total_units = 0
    total_docs = 0
    batch_num = 0
    t0 = time.time()

    logger.info("Starting ingestion from %s ...", source)
    while True:
        batch_num += 1
        stats = process_ingestion_batch(state)
        units = stats["processed_units"]
        docs = stats["inserted_docs"]
        events = stats.get("event_count", 0)
        buffered = stats.get("buffered_events", 0)
        total_units += units
        total_docs += docs

        if units > 0 or docs > 0:
            logger.info(
                "Batch %d: rows=%d events=%d docs=%d buffered=%d (cumulative: %d rows, %d docs)",
                batch_num,
                units,
                events,
                docs,
                buffered,
                total_units,
                total_docs,
            )

        if units == 0 and docs == 0:
            break

    _flush_state(state)
    elapsed = time.time() - t0
    logger.info(
        "Ingestion complete: %d rows -> %d documents in %.1fs. Index saved to disk.",
        total_units,
        total_docs,
        elapsed,
    )


if __name__ == "__main__":
    main()
