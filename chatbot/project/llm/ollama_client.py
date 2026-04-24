from __future__ import annotations

import logging
import re
import threading
from typing import Any

import requests

from project.settings import (
    OLLAMA_ENDPOINT,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT_SECONDS,
    WARMUP_PROMPT,
)


logger = logging.getLogger(__name__)
_MODEL_LOCK = threading.Lock()
_RESOLVED_MODEL_NAME: str | None = None
_AVAILABLE_MODELS: list[str] | None = None


def _sanitize_response(text: str) -> str:
    cleaned = text or ""
    if "<|endoftext|>" in cleaned:
        cleaned = cleaned.split("<|endoftext|>", 1)[0]
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = cleaned.replace("<|im_start|>assistant", "")
    cleaned = cleaned.replace("<|im_start|>user", "")
    cleaned = cleaned.replace("<|im_end|>", "")
    cleaned = cleaned.replace("<|endoftext|>", "")
    return cleaned.strip()


def _api_base_url() -> str:
    endpoint = OLLAMA_ENDPOINT.rstrip("/")
    if endpoint.endswith("/api/generate"):
        return endpoint[: -len("/api/generate")]
    return endpoint


def _post_ollama(payload: dict[str, Any], endpoint: str | None = None) -> dict[str, Any]:
    response = requests.post(
        endpoint or OLLAMA_ENDPOINT,
        json=payload,
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected Ollama response format")
    return data


def _fetch_available_models() -> list[str]:
    global _AVAILABLE_MODELS
    cached = _AVAILABLE_MODELS
    if cached is not None:
        return cached

    url = f"{_api_base_url()}/api/tags"
    response = requests.get(url, timeout=min(OLLAMA_TIMEOUT_SECONDS, 15))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Unexpected Ollama tags response format")

    models = payload.get("models", [])
    if not isinstance(models, list):
        return []

    names = []
    for model in models:
        if not isinstance(model, dict):
            continue
        name = str(model.get("name", "")).strip()
        if name:
            names.append(name)
    _AVAILABLE_MODELS = names
    return names


def _resolve_model_name() -> str:
    global _RESOLVED_MODEL_NAME
    resolved = _RESOLVED_MODEL_NAME
    if resolved is not None:
        return resolved

    with _MODEL_LOCK:
        if _RESOLVED_MODEL_NAME is not None:
            return _RESOLVED_MODEL_NAME

        available = _fetch_available_models()
        if not available:
            _RESOLVED_MODEL_NAME = OLLAMA_MODEL
            return _RESOLVED_MODEL_NAME

        normalized = {name.lower(): name for name in available}
        exact = normalized.get(OLLAMA_MODEL.lower())
        if exact is None:
            available_text = ", ".join(sorted(available))
            raise RuntimeError(
                f"Configured Ollama model {OLLAMA_MODEL} is not installed. "
                f"Available models: {available_text}"
            )

        _RESOLVED_MODEL_NAME = exact
        return _RESOLVED_MODEL_NAME


def _request_payload(prompt: str) -> dict[str, Any]:
    return {
        "model": _resolve_model_name(),
        "prompt": prompt,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
    }


def warm_ollama_model() -> None:
    try:
        payload = _request_payload(WARMUP_PROMPT)
        _post_ollama(payload)
        logger.info("Ollama model ready: %s", payload["model"])
    except RuntimeError as exc:
        logger.error("Ollama model resolution failed: %s", exc)
    except requests.RequestException as exc:
        logger.warning("Ollama warmup failed: %s", exc)


def generate_answer_with_ollama(prompt: str) -> str:
    payload: dict[str, Any] = {}
    try:
        payload = _request_payload(prompt)
        data = _post_ollama(payload)
        answer = _sanitize_response(str(data.get("response", "")))
        return answer or "No activity found"
    except RuntimeError as exc:
        return str(exc)
    except requests.RequestException as exc:
        try:
            available = _fetch_available_models()
        except Exception:
            available = []
        available_text = ", ".join(available[:5]) if available else "unknown"
        resolved_model = str(payload.get("model") or "unknown")
        return (
            f"Ollama request failed: {exc}. "
            f"Configured model={OLLAMA_MODEL}, resolved model={resolved_model}, available models={available_text}"
        )
