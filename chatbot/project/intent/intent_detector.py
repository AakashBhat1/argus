from __future__ import annotations

import json
import re
import threading
from pathlib import Path


_INTENT_LOCK = threading.Lock()
_INTENT_MAP: dict[str, list[str]] | None = None


def _default_intent_map() -> dict[str, list[str]]:
    return {
        "greeting": ["hi", "hello", "hey", "yo", "good morning", "good evening"],
        "help": ["help", "what can you do", "capabilities", "how does this work", "what questions can i ask"],
    }


def initialize_intent_detector(config_path: str | Path | None = None) -> None:
    global _INTENT_MAP
    if _INTENT_MAP is not None:
        return

    with _INTENT_LOCK:
        if _INTENT_MAP is not None:
            return

        base_dir = Path(__file__).resolve().parents[1]
        intent_path = Path(config_path) if config_path is not None else (base_dir / "config" / "intent_config.json")

        intent_map = _default_intent_map()
        try:
            loaded = json.loads(intent_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                normalized: dict[str, list[str]] = {}
                for intent_name, phrases in loaded.items():
                    if not isinstance(phrases, list):
                        continue
                    normalized[intent_name] = [
                        str(phrase).strip().lower() for phrase in phrases if str(phrase).strip()
                    ]
                if normalized:
                    intent_map = normalized
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        _INTENT_MAP = intent_map


def detect_intent(query: str) -> str:
    initialize_intent_detector()
    query_lc = (query or "").lower()
    intent_map = _INTENT_MAP or _default_intent_map()

    def contains_phrase(phrase: str) -> bool:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        return re.search(pattern, query_lc) is not None

    for phrase in intent_map.get("greeting", []):
        if contains_phrase(phrase):
            return "greeting"

    for phrase in intent_map.get("help", []):
        if contains_phrase(phrase):
            return "help"

    return "activity_query"
