from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
_ENV_FILE = PROJECT_DIR.parent / ".env"
if _ENV_FILE.is_file():
    load_dotenv(_ENV_FILE, override=False)
REPO_ROOT = PROJECT_DIR.parent
WORKSPACE_ROOT = REPO_ROOT.parent


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_data_dir() -> Path:
    env_path = os.getenv("CHATBOT_DATA_DIR")
    if not env_path:
        return PROJECT_DIR / "data"
    path = Path(env_path).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_DIR / path


def _resolve_log_path() -> Path:
    env_path = os.getenv("ROI_EVENTS_PATH")
    # When ROI_EVENTS_PATH is explicitly configured (e.g. Docker shared volume),
    # always use it even if the file is not created yet.
    if env_path:
        return Path(env_path).expanduser()

    candidates = [
        PROJECT_DIR / "roi_events.jsonl",
        REPO_ROOT / "backend" / "yolo_classifier" / "intrusion_monitor" / "roi_events.jsonl",
        WORKSPACE_ROOT / "backend" / "yolo_classifier" / "intrusion_monitor" / "roi_events.jsonl",
    ]
    for path in [candidate for candidate in candidates if candidate is not None]:
        if path.exists():
            return path
    return PROJECT_DIR / "roi_events.jsonl"


ROI_EVENTS_PATH = _resolve_log_path()

DATA_DIR = _resolve_data_dir()
FAISS_INDEX_PATH = DATA_DIR / "faiss.index"
DOCUMENTS_PATH = DATA_DIR / "documents.pkl"
EMBEDDINGS_PATH = DATA_DIR / "embeddings.npy"
CHECKPOINT_PATH = DATA_DIR / "checkpoint.txt"
POSTGRES_CHECKPOINT_PATH = DATA_DIR / "checkpoint.json"
LEGACY_POSTGRES_CHECKPOINT_PATH = DATA_DIR / "checkpoint_postgres.json"

INGESTION_SOURCE = os.getenv("INGESTION_SOURCE", "postgres").strip().lower()
CHATBOT_DATABASE_URL = os.getenv("CHATBOT_DATABASE_URL", "").strip()
CHATBOT_TENANT_ID = os.getenv("CHATBOT_TENANT_ID", "1").strip() or "1"

EMBEDDING_MODEL_NAME = os.getenv(
    "EMBEDDING_MODEL_NAME",
    "BAAI/bge-small-en-v1.5",
)
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "256"))
EMBEDDING_LOCAL_FILES_ONLY = _env_flag("EMBEDDING_LOCAL_FILES_ONLY", "1")
EMBEDDING_QUERY_PREFIX = os.getenv("EMBEDDING_QUERY_PREFIX", "").strip()
EMBEDDING_DOCUMENT_PREFIX = os.getenv("EMBEDDING_DOCUMENT_PREFIX", "").strip()
EMBEDDING_NORMALIZE = _env_flag("EMBEDDING_NORMALIZE", "1")

RERANKER_ENABLED = _env_flag("RERANKER_ENABLED", "1")
RERANKER_MODEL_NAME = os.getenv("RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2").strip()
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", EMBEDDING_DEVICE).strip() or EMBEDDING_DEVICE
RERANKER_LOCAL_FILES_ONLY = _env_flag(
    "RERANKER_LOCAL_FILES_ONLY",
    "1" if EMBEDDING_LOCAL_FILES_ONLY else "0",
)
RERANKER_BATCH_SIZE = int(os.getenv("RERANKER_BATCH_SIZE", "16"))

INGESTION_INTERVAL_SECONDS = int(os.getenv("INGESTION_INTERVAL_SECONDS", "5"))
MAX_LINES_PER_BATCH = int(os.getenv("MAX_LINES_PER_BATCH", "5000"))
VECTOR_FLUSH_INTERVAL_SECONDS = int(os.getenv("VECTOR_FLUSH_INTERVAL_SECONDS", "60"))
VECTOR_FLUSH_DOC_THRESHOLD = int(os.getenv("VECTOR_FLUSH_DOC_THRESHOLD", "1000"))
TEMPORAL_WINDOW_SECONDS = int(os.getenv("TEMPORAL_WINDOW_SECONDS", "300"))
TEMPORAL_WINDOW_STRIDE_SECONDS = int(os.getenv("TEMPORAL_WINDOW_STRIDE_SECONDS", "300"))
TEMPORAL_WINDOW_MAX_EVENTS = int(os.getenv("TEMPORAL_WINDOW_MAX_EVENTS", "25"))
TEMPORAL_WINDOW_IDLE_FLUSH_SECONDS = int(os.getenv("TEMPORAL_WINDOW_IDLE_FLUSH_SECONDS", "30"))
VISIT_MAX_GAP_SECONDS = int(os.getenv("VISIT_MAX_GAP_SECONDS", "90"))

TOP_K = int(os.getenv("TOP_K", "5"))
RETRIEVAL_CANDIDATE_K = int(os.getenv("RETRIEVAL_CANDIDATE_K", "20"))
VECTOR_SEARCH_OVERSAMPLE = int(os.getenv("VECTOR_SEARCH_OVERSAMPLE", "60"))
SEMANTIC_WEIGHT = float(os.getenv("SEMANTIC_WEIGHT", "0.50"))
RERANK_WEIGHT = float(os.getenv("RERANK_WEIGHT", "0.30"))
RECENCY_WEIGHT = float(os.getenv("RECENCY_WEIGHT", "0.20"))
RECENCY_HALF_LIFE_HOURS = float(os.getenv("RECENCY_HALF_LIFE_HOURS", "24"))
QUERY_CACHE_ENABLED = _env_flag("QUERY_CACHE_ENABLED", "1")
QUERY_CACHE_TTL_SECONDS = int(os.getenv("QUERY_CACHE_TTL_SECONDS", "30"))
QUERY_CACHE_MAX_ITEMS = int(os.getenv("QUERY_CACHE_MAX_ITEMS", "256"))

FAISS_INDEX_TYPE = os.getenv("FAISS_INDEX_TYPE", "flat").strip().lower()
FAISS_HNSW_M = int(os.getenv("FAISS_HNSW_M", "32"))
FAISS_HNSW_EF_SEARCH = int(os.getenv("FAISS_HNSW_EF_SEARCH", "64"))
FAISS_HNSW_EF_CONSTRUCTION = int(os.getenv("FAISS_HNSW_EF_CONSTRUCTION", "80"))
FAISS_IVF_NLIST = int(os.getenv("FAISS_IVF_NLIST", "256"))
FAISS_PQ_M = int(os.getenv("FAISS_PQ_M", "32"))
FAISS_PQ_BITS = int(os.getenv("FAISS_PQ_BITS", "8"))

INDEX_RELOAD_SECONDS = int(os.getenv("INDEX_RELOAD_SECONDS", "30"))

OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "24h")
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120"))

WARMUP_PROMPT = "Reply with OK."

MAX_EVENTS_FOR_PROMPT = int(os.getenv("MAX_EVENTS_FOR_PROMPT", "12"))
MAX_DOCUMENTS_FOR_PROMPT = int(os.getenv("MAX_DOCUMENTS_FOR_PROMPT", "5"))
