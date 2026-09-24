"""Synthetic parking-lot frames and clips for tests.

The suite used to depend on a third-party stock clip and frames cut from it.
These generators produce deterministic stand-ins with the same geometry
(a 768x432 lot with a row of 13 bays along the bottom), so tests exercise the
same code paths without shipping licensed media.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

WIDTH = 768
HEIGHT = 432
BAYS = 13
ROW_TOP = 0.62
ROW_BOTTOM = 0.96


def bay_polygon(index: int) -> np.ndarray:
    """Normalized quad of bottom-row bay ``index`` (0-based)."""
    x1, x2 = index / BAYS, (index + 1) / BAYS
    return np.array(
        [[x1, ROW_TOP], [x2, ROW_TOP], [x2, ROW_BOTTOM], [x1, ROW_BOTTOM]],
        dtype=np.float32,
    )


def _draw_car(frame: np.ndarray, x1: int, y1: int, x2: int, y2: int, rng: np.random.Generator) -> None:
    """A textured car-like blob: body, windows, wheels and panel edges."""
    # Light body with dark panel seams: strong, deterministic local contrast,
    # which is what the texture fallback measures.
    shade = int(rng.integers(185, 215))
    colour = (shade, shade, shade + 5)
    pad_x = max(2, (x2 - x1) // 10)
    pad_y = max(2, (y2 - y1) // 12)
    cv2.rectangle(frame, (x1 + pad_x, y1 + pad_y), (x2 - pad_x, y2 - pad_y), colour, -1)
    # Windscreen and rear window (dark), panel lines and wheels.
    h = y2 - y1
    cv2.rectangle(frame, (x1 + 2 * pad_x, y1 + h // 5), (x2 - 2 * pad_x, y1 + 2 * h // 5), (25, 25, 30), -1)
    cv2.rectangle(frame, (x1 + 2 * pad_x, y1 + 3 * h // 5), (x2 - 2 * pad_x, y1 + 4 * h // 5), (25, 25, 30), -1)
    for yy in range(y1 + pad_y, y2 - pad_y, 5):
        cv2.line(frame, (x1 + pad_x, yy), (x2 - pad_x, yy), (15, 15, 15), 2)
    for cx in (x1 + pad_x, x2 - pad_x):
        for cy in (y1 + h // 4, y1 + 3 * h // 4):
            cv2.circle(frame, (cx, cy), max(2, pad_x), (10, 10, 10), -1)


def parking_lot_frame(occupied: Iterable[int] = (), seed: int = 0) -> np.ndarray:
    """BGR frame of the lot with cars in the given bottom-row bays."""
    rng = np.random.default_rng(seed)
    frame = np.full((HEIGHT, WIDTH, 3), 118, dtype=np.uint8)
    noise = rng.normal(0.0, 2.0, size=frame.shape)
    frame = np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    top, bottom = int(ROW_TOP * HEIGHT), int(ROW_BOTTOM * HEIGHT)
    for i in range(BAYS + 1):
        x = min(WIDTH - 1, round(i * WIDTH / BAYS))
        cv2.line(frame, (x, top - 6), (x, bottom + 6), (150, 150, 150), 1)
    for index in occupied:
        x1 = round(index * WIDTH / BAYS) + 3
        x2 = round((index + 1) * WIDTH / BAYS) - 3
        _draw_car(frame, x1, top + 4, x2, bottom - 4, rng)
    return frame


def write_clip(path: Path, frames: list[np.ndarray], fps: float = 10.0) -> Path:
    """Write ``frames`` to an mp4 (mp4v) file and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not open an mp4v writer")
    try:
        for frame in frames:
            writer.write(frame)
    finally:
        writer.release()
    return path


def sample_clip(path: Path) -> Path:
    """A short lot clip: bay 1 occupied, then bay 0 fills as well."""
    frames = [parking_lot_frame({1}, seed=1)] * 10 + [parking_lot_frame({0, 1}, seed=1)] * 10
    return write_clip(path, frames)
