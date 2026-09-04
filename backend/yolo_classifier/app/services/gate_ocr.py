"""Gate ROI OCR trigger — once-per-track plate recognition.

Used by IntrusionPipeline when a camera has role gate_entry/gate_exit.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import cv2
import numpy as np

from app.config import get_settings
from app.database import get_session_factory
from app.models import VehicleProfile
from app.services.authorization import authorization_registry
from app.services.ocr_service import recognize_plate
from app.services import parking_service
from app.services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

GATE_ROLES = frozenset({"gate_entry", "gate_exit"})


def point_in_gate_roi(
    cx: float,
    cy: float,
    gate_roi: Any,
    frame_w: int,
    frame_h: int,
) -> bool:
    """Return True if (cx, cy) is inside the camera's gate_roi polygon.

    gate_roi formats accepted:
    - list of [x, y] or {x, y} points (normalized 0-1 if all values <= 1)
    - list of flat numbers [x1,y1,x2,y2,...]
    """
    if not gate_roi or frame_w <= 0 or frame_h <= 0:
        return False

    points: list[list[float]] = []
    try:
        if isinstance(gate_roi, dict) and "points" in gate_roi:
            gate_roi = gate_roi["points"]
        if not isinstance(gate_roi, (list, tuple)) or len(gate_roi) < 3:
            return False

        # Flat [x1,y1,x2,y2,...]
        if gate_roi and isinstance(gate_roi[0], (int, float)):
            flat = list(gate_roi)
            for i in range(0, len(flat) - 1, 2):
                points.append([float(flat[i]), float(flat[i + 1])])
        else:
            for p in gate_roi:
                if isinstance(p, dict):
                    points.append([float(p.get("x", 0)), float(p.get("y", 0))])
                elif isinstance(p, (list, tuple)) and len(p) >= 2:
                    points.append([float(p[0]), float(p[1])])
    except (TypeError, ValueError):
        return False

    if len(points) < 3:
        return False

    arr = np.array(points, dtype=np.float32)
    # Normalized if all coords in [0, 1]
    if float(arr.max()) <= 1.0:
        arr[:, 0] *= float(frame_w)
        arr[:, 1] *= float(frame_h)

    polygon = arr.reshape((-1, 1, 2))
    return cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) >= 0


def crop_vehicle(frame: np.ndarray, obj: dict) -> Optional[np.ndarray]:
    if frame is None or frame.size == 0:
        return None
    h, w = frame.shape[:2]
    x = int(max(0, obj.get("bbox_x", 0)))
    y = int(max(0, obj.get("bbox_y", 0)))
    bw = int(max(1, obj.get("bbox_w", 0)))
    bh = int(max(1, obj.get("bbox_h", 0)))
    x2 = min(w, x + bw)
    y2 = min(h, y + bh)
    if x2 <= x or y2 <= y:
        return None
    return frame[y:y2, x:x2].copy()


class GateOcrTrigger:
    """Stateful per-camera gate OCR de-duplicator (once per track_id)."""

    def __init__(self, camera_id: str, tenant_id: str):
        self.camera_id = str(camera_id)
        self.tenant_id = tenant_id
        self._seen_tracks: set[str] = set()
        self._camera_role: Optional[str] = None
        self._gate_roi: Any = None
        self._meta_loaded = False
        self._pending: set[Any] = set()
        # The pipeline runs in a worker thread, so we need the owning event
        # loop to hand OCR work back to. Captured here when constructed on
        # the loop, or set later via bind_loop().
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        self._inactive_reason_logged: Optional[str] = None
        self._vehicles_in_gate = 0
        self._plates_read = 0
        self._last_plate: Optional[str] = None
        self._last_error: Optional[str] = None

    def reset(self) -> None:
        self._seen_tracks.clear()
        self._meta_loaded = False
        for t in list(self._pending):
            t.cancel()
        self._pending.clear()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the event loop that owns this camera's stream.

        Also kicks off the camera-metadata load so ``status()`` is meaningful
        before the first vehicle shows up.
        """
        self._loop = loop
        if self._meta_loaded or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(self.load_camera_meta())
        else:
            asyncio.run_coroutine_threadsafe(self.load_camera_meta(), loop)

    def update_meta(self, role: Optional[str], gate_roi: Any) -> None:
        """Hot-swap camera role / gate polygon without restarting the stream."""
        self._camera_role = (role or "surveillance").lower()
        self._gate_roi = gate_roi
        self._meta_loaded = True
        self._inactive_reason_logged = None
        # A new gate polygon means vehicles already seen may now cross it.
        self._seen_tracks.clear()
        logger.info(
            "Gate OCR config updated for cam=%s role=%s gate_roi=%s",
            self.camera_id,
            self._camera_role,
            "set" if gate_roi else "none",
        )

    def inactive_reason(self) -> Optional[str]:
        """Why OCR will not fire on this camera right now (None = active)."""
        if not self._meta_loaded:
            return "camera metadata not loaded yet"
        if self._camera_role not in GATE_ROLES:
            return (
                f"camera role is '{self._camera_role or 'surveillance'}'; "
                "set it to gate_entry or gate_exit"
            )
        if not self._gate_roi:
            return "no gate polygon drawn (Scene Setup → Gate tab)"
        return None

    def status(self) -> dict:
        reason = self.inactive_reason()
        return {
            "active": reason is None,
            "reason": reason,
            "role": self._camera_role,
            "has_gate_roi": bool(self._gate_roi),
            "vehicles_in_gate": self._vehicles_in_gate,
            "plates_read": self._plates_read,
            "last_plate": self._last_plate,
            "last_error": self._last_error,
        }

    async def load_camera_meta(self) -> None:
        if self._meta_loaded:
            return
        from sqlalchemy import select
        from app.models import Camera

        try:
            factory = get_session_factory()
            async with factory() as session:
                res = await session.execute(
                    select(Camera).where(Camera.id == self.camera_id)
                )
                cam = res.scalar_one_or_none()
                if cam:
                    self._camera_role = (cam.role or "surveillance").lower()
                    self._gate_roi = cam.gate_roi
                    # Prefer DB tenant if set
                    if cam.tenant_id:
                        self.tenant_id = cam.tenant_id
            self._meta_loaded = True
        except Exception as exc:
            logger.debug("Gate OCR meta load failed for %s: %s", self.camera_id, exc)
            self._meta_loaded = True  # avoid hammering

    def process_frame(
        self,
        tracked_objects: list[dict],
        frame: np.ndarray,
    ) -> None:
        """Sync entry from pipeline; schedules async OCR work when needed.

        Safe to call from a worker thread (the pipeline runs in an executor):
        work is handed to the bound event loop thread-safely.
        """
        if frame is None:
            return
        settings = get_settings()
        trigger_classes = {
            c.strip().lower() for c in settings.PARKING_OCR_TRIGGER_CLASSES
        }

        # Cheap pre-filter: nothing to do without a vehicle in view. Also
        # avoids copying the frame every tick.
        if not any(
            str(o.get("class_label", "")).strip().lower() in trigger_classes
            for o in tracked_objects
        ):
            return
        # Bounded backlog: OCR is slow relative to frame rate.
        if len(self._pending) >= 4:
            return

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        loop = running or self._loop
        if loop is None or loop.is_closed():
            if self._inactive_reason_logged != "no-loop":
                logger.warning(
                    "Gate OCR for cam=%s has no event loop bound; call bind_loop()",
                    self.camera_id,
                )
                self._inactive_reason_logged = "no-loop"
            return

        coro = self._evaluate_async(tracked_objects, frame.copy(), trigger_classes)
        if running is loop:
            handle: Any = loop.create_task(coro)
        else:
            handle = asyncio.run_coroutine_threadsafe(coro, loop)
        self._pending.add(handle)
        handle.add_done_callback(self._pending.discard)

    async def _evaluate_async(
        self,
        tracked_objects: list[dict],
        frame: np.ndarray,
        trigger_classes: set[str],
    ) -> None:
        await self.load_camera_meta()
        reason = self.inactive_reason()
        if reason is not None:
            # Log once per configuration so a silent "OCR never fires" is
            # diagnosable from the server log.
            if self._inactive_reason_logged != reason and any(
                str(o.get("class_label", "")).lower() in trigger_classes for o in tracked_objects
            ):
                logger.info("Gate OCR inactive for cam=%s: %s", self.camera_id, reason)
                self._inactive_reason_logged = reason
            return

        fh, fw = frame.shape[:2]
        for obj in tracked_objects:
            label = str(obj.get("class_label", "")).strip().lower()
            if label not in trigger_classes:
                continue
            track_key = str(obj.get("object_id", ""))
            if not track_key or track_key in self._seen_tracks:
                continue

            cx = float(obj.get("bbox_x", 0)) + float(obj.get("bbox_w", 0)) / 2.0
            cy = float(obj.get("bbox_y", 0)) + float(obj.get("bbox_h", 0)) / 2.0
            if not point_in_gate_roi(cx, cy, self._gate_roi, fw, fh):
                continue

            # Mark seen BEFORE OCR so we only fire once per track
            self._seen_tracks.add(track_key)
            self._vehicles_in_gate += 1
            crop = crop_vehicle(frame, obj)
            if crop is None:
                continue

            loop = asyncio.get_running_loop()
            plate, state, conf = await loop.run_in_executor(None, recognize_plate, crop)
            if not plate:
                self._last_error = f"no readable plate on track {track_key}"
                logger.info(
                    "Gate OCR: vehicle track=%s entered gate on cam=%s but no plate was read (state=%s)",
                    track_key,
                    self.camera_id,
                    state,
                )
                continue
            self._plates_read += 1
            self._last_plate = plate
            self._last_error = None

            try:
                factory = get_session_factory()
                async with factory() as session:
                    det = await parking_service.record_detected_plate(
                        session,
                        self.tenant_id,
                        plate,
                        state=state,
                        confidence=conf,
                        camera_id=self.camera_id,
                        track_id=track_key,
                    )
                    # Auto-assign on gate_entry
                    assign_result = None
                    if self._camera_role == "gate_entry":
                        assign_result = await parking_service.assign_space(
                            session, self.tenant_id, plate
                        )
                    profile = await session.get(VehicleProfile, det.vehicle_id) if det.vehicle_id else None
                    await session.commit()

                # Tell the site authorization registry who just arrived so that
                # persons stepping out of this vehicle inherit its status.
                try:
                    authorization_registry.register_vehicle_plate(
                        self.tenant_id,
                        camera_id=self.camera_id,
                        track_id=int(track_key),
                        plate_text=plate,
                        profile_type=profile.profile_type if profile else None,
                        owner_name=profile.owner_name if profile else None,
                    )
                except (TypeError, ValueError):
                    logger.debug("Gate OCR: could not register plate for track %s", track_key)

                payload = {
                    "type": "parking",
                    "data": {
                        "event": "plate_detected",
                        "plate_text": plate,
                        "state": state,
                        "confidence": conf,
                        "camera_id": self.camera_id,
                        "track_id": track_key,
                        "detection_id": det.id,
                        "assign": assign_result,
                    },
                }
                # Serialize datetime for WS
                if assign_result and assign_result.get("entry_time") is not None:
                    et = assign_result["entry_time"]
                    if hasattr(et, "isoformat"):
                        payload["data"]["assign"] = {
                            **assign_result,
                            "entry_time": et.isoformat(),
                        }

                await ws_manager.broadcast_to_channel(
                    self.tenant_id, "parking", payload
                )
                logger.info(
                    "Gate OCR: plate=%s track=%s cam=%s tenant=%s",
                    plate,
                    track_key,
                    self.camera_id,
                    self.tenant_id,
                )
            except Exception as exc:
                logger.exception("Gate OCR persist failed: %s", exc)
