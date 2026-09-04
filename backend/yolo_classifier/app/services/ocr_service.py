"""RapidOCR singleton for license-plate recognition.

Load once; expose ``recognize_plate(crop) -> (plate, state, conf)``.
Sync OCR is run in a thread executor when called from async code.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

import cv2
import numpy as np

from app.config import get_settings

logger = logging.getLogger(__name__)

# Indian state code mapping
STATE_CODES = {
    "AN": "Andaman & Nicobar", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh",
    "AS": "Assam", "BR": "Bihar", "CH": "Chandigarh", "CT": "Chhattisgarh",
    "DN": "Dadra & Nagar Haveli", "DD": "Daman & Diu", "DL": "Delhi",
    "GA": "Goa", "GJ": "Gujarat", "HR": "Haryana", "HP": "Himachal Pradesh",
    "JK": "Jammu & Kashmir", "JH": "Jharkhand", "KA": "Karnataka",
    "KL": "Kerala", "LA": "Ladakh", "LD": "Lakshadweep", "MP": "Madhya Pradesh",
    "MH": "Maharashtra", "MN": "Manipur", "ML": "Meghalaya", "MZ": "Mizoram",
    "NL": "Nagaland", "OD": "Odisha", "PB": "Punjab", "PY": "Puducherry",
    "RJ": "Rajasthan", "SK": "Sikkim", "TN": "Tamil Nadu", "TS": "Telangana",
    "TR": "Tripura", "UP": "Uttar Pradesh", "UK": "Uttarakhand", "WB": "West Bengal",
}

# Standard Indian plate: XX00X0000 or XX00XX0000
PLATE_REGEX = re.compile(r"^[A-Z]{2}\d{2}[A-Z]{1,2}\d{4}$")

_engine = None
_engine_lock = asyncio.Lock() if False else None  # placeholder; engine is sync-init


def _get_engine():
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
            logger.info("RapidOCR engine loaded (singleton)")
        except Exception as exc:
            logger.error("Failed to load RapidOCR: %s", exc)
            raise
    return _engine


def find_state(plate_text: str) -> str:
    if not plate_text or len(plate_text) < 2:
        return "Unknown"
    return STATE_CODES.get(plate_text[:2].upper(), "Unknown")


def normalize_plate_text(text: str) -> str:
    """Strip non-alphanumeric and uppercase."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def is_valid_plate(plate_text: str) -> bool:
    """Validate against Indian plate regex."""
    return bool(PLATE_REGEX.match(normalize_plate_text(plate_text)))


# Loose plausibility gate for plates that don't match the strict Indian
# format (BH-series, older formats, foreign plates): at least 6 characters
# containing both letters and digits. Rejects OCR noise like "L" or "1234".
MIN_PLATE_CHARS = 6


def is_plausible_plate(plate_text: str) -> bool:
    text = normalize_plate_text(plate_text)
    if is_valid_plate(text):
        return True
    if len(text) < MIN_PLATE_CHARS or len(text) > 12:
        return False
    return any(c.isalpha() for c in text) and any(c.isdigit() for c in text)


def preprocess_plate_image(img_bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.convertScaleAbs(gray, alpha=1.5, beta=10)
    h, w = enhanced.shape[:2]
    if w > 0 and h > 0:
        new_w = 400
        new_h = max(1, int(h * new_w / w))
        enhanced = cv2.resize(enhanced, (new_w, new_h))
    return enhanced


def recognize_plate(img_bgr: np.ndarray) -> tuple[Optional[str], str, float]:
    """Run OCR on a vehicle/plate crop.

    Returns:
        (plate_text, state, confidence) — plate_text is None on failure.
    """
    if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
        return None, "Unknown", 0.0

    settings = get_settings()
    try:
        engine = _get_engine()
        result, _elapse = engine(img_bgr)
        if not result:
            return None, "Unknown", 0.0

        text = " ".join([line[1] for line in result]).strip()
        confidence = 0.0
        try:
            confidences = [float(line[2]) for line in result]
            if confidences:
                confidence = sum(confidences) / len(confidences)
        except (IndexError, ValueError, TypeError):
            pass

        text = "".join(c for c in text if c.isalnum() or c.isspace()).strip()
        plate = normalize_plate_text(text)
        if not plate:
            return None, "Unknown", 0.0

        if not is_plausible_plate(plate):
            logger.info("OCR rejected implausible plate text %r (conf %.2f)", plate, confidence)
            return None, "Rejected", round(float(confidence), 3)

        if confidence < settings.PARKING_OCR_CONFIDENCE_THRESHOLD:
            logger.info(
                "OCR rejected %s: confidence %.2f below threshold %.2f",
                plate,
                confidence,
                settings.PARKING_OCR_CONFIDENCE_THRESHOLD,
            )
            return None, "LowConfidence", round(float(confidence), 3)

        state = find_state(plate)
        return plate, state, round(float(confidence), 3)
    except Exception as exc:
        logger.warning("OCR error: %s", exc)
        return None, "Error", 0.0


async def recognize_plate_async(
    img_bgr: np.ndarray,
) -> tuple[Optional[str], str, float]:
    """Async wrapper — runs sync RapidOCR off the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, recognize_plate, img_bgr)
