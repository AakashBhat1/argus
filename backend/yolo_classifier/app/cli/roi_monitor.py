"""Local ROI monitor CLI backed by the unified YOLO detection pipeline."""

from __future__ import annotations

import argparse
import json
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from app.config import get_settings
from app.detection.roi import RoiZoneRepository, write_default_zones_config
from app.services.intrusion_pipeline import IntrusionPipeline
from app.utils import utc_now


def _open_capture(camera_index: int) -> cv2.VideoCapture:
    if platform.system().lower() == "windows":
        return cv2.VideoCapture(int(camera_index), cv2.CAP_DSHOW)
    return cv2.VideoCapture(int(camera_index))


def _capture_roi_image(camera_index: int, out_path: str = "roi_reference.png") -> None:
    cap = _open_capture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open camera index {camera_index}")
    ret, frame = cap.read()
    cap.release()
    if not ret:
        raise RuntimeError("Failed to capture frame for ROI reference image.")
    cv2.imwrite(out_path, frame)
    print(f"[INFO] Saved ROI reference image to '{out_path}'.")


def _load_zones_payload(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        return list(payload.get("zones", []))
    if isinstance(payload, list):
        return list(payload)
    return []


def _save_zones_payload(path: Path, payload: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _define_zone_interactive(camera_index: int, config_path: Optional[str] = None) -> None:
    settings = get_settings()
    repo = RoiZoneRepository(config_path=config_path)
    cfg_path = repo.config_path

    cap = _open_capture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open camera index {camera_index}")
    ret, frame = cap.read()
    cap.release()
    if not ret:
        raise RuntimeError("Failed to capture frame for interactive ROI definition.")

    frame = cv2.resize(frame, (settings.ROI_REFERENCE_WIDTH, settings.ROI_REFERENCE_HEIGHT))
    window_name = "Define ROI - click points"
    points: list[tuple[int, int]] = []

    def mouse_callback(event, x, y, flags, userdata):
        del flags, userdata
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((x, y))

    cv2.namedWindow(window_name)
    cv2.setMouseCallback(window_name, mouse_callback)

    try:
        while True:
            display = frame.copy()
            for idx, (x, y) in enumerate(points):
                cv2.circle(display, (x, y), 4, (0, 255, 0), -1)
                cv2.putText(
                    display,
                    str(idx + 1),
                    (x + 5, y - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    1,
                    lineType=cv2.LINE_AA,
                )

            if len(points) >= 3:
                polygon = np.array(points, dtype=np.int32).reshape((-1, 1, 2))
                cv2.polylines(display, [polygon], isClosed=True, color=(0, 255, 255), thickness=2)
                info_text = "Press 's' to save polygon, 'r' to reset, ESC to cancel"
            else:
                info_text = "Click at least 3 points, then 's' to save, 'r' reset, ESC cancel"

            cv2.putText(
                display,
                info_text,
                (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                2,
                lineType=cv2.LINE_AA,
            )

            cv2.imshow(window_name, display)
            key = cv2.waitKey(50) & 0xFF
            if key == 27:
                print("[INFO] ROI definition cancelled.")
                return
            if key == ord("r"):
                points.clear()
            if key == ord("s") and len(points) >= 3:
                payload = _load_zones_payload(cfg_path)
                next_id = (
                    max(int(item.get("zone_id", 0)) for item in payload) + 1
                    if payload
                    else 1
                )
                payload.append(
                    {
                        "zone_id": next_id,
                        "name": f"Zone-{next_id}",
                        "points": [[int(x), int(y)] for x, y in points],
                        "threshold_sec": settings.ROI_DEFAULT_DWELL_SEC,
                        "color": [0, 255, 255],
                        "reference_width": settings.ROI_REFERENCE_WIDTH,
                        "reference_height": settings.ROI_REFERENCE_HEIGHT,
                    }
                )
                _save_zones_payload(cfg_path, payload)
                print(f"[INFO] Added zone {next_id} to '{cfg_path}'.")
                return
    finally:
        try:
            cv2.destroyWindow(window_name)
        except cv2.error:
            pass


def _draw_overlay(
    frame: np.ndarray,
    camera_id: str,
    zone_repo: RoiZoneRepository,
    tracked_objects: list[dict],
    intrusion_events: list,
) -> None:
    frame_h, frame_w = frame.shape[:2]
    zones = zone_repo.zones_for_camera(camera_id)
    active_zone_ids = {event.zone_id for event in intrusion_events}

    for zone in zones:
        polygon = zone.to_pixel_points(frame_w, frame_h).astype(np.int32).reshape((-1, 1, 2))
        color = (0, 0, 255) if zone.zone_id in active_zone_ids else zone.color
        cv2.polylines(frame, [polygon], isClosed=True, color=color, thickness=2)
        tx, ty = int(polygon[0, 0, 0]), int(polygon[0, 0, 1]) - 8
        cv2.putText(
            frame,
            zone.name,
            (tx, max(18, ty)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            lineType=cv2.LINE_AA,
        )

    persons_in_roi = 0
    for obj in tracked_objects:
        x1 = int(obj.get("bbox_x", 0))
        y1 = int(obj.get("bbox_y", 0))
        w = int(obj.get("bbox_w", 0))
        h = int(obj.get("bbox_h", 0))
        x2 = x1 + w
        y2 = y1 + h
        class_label = str(obj.get("class_label", ""))
        object_id = int(obj.get("object_id", -1))
        confidence = float(obj.get("confidence", 0.0))
        inside_roi = bool(obj.get("inside_roi", False))
        intrusion = bool(obj.get("intrusion", False))
        dwell = float(obj.get("max_roi_dwell_sec", 0.0))

        if class_label == "person" and inside_roi:
            persons_in_roi += 1

        if intrusion:
            color = (0, 0, 255)
        elif inside_roi:
            color = (0, 255, 255)
        else:
            color = (0, 255, 0)

        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"ID {object_id} {class_label} {confidence:.2f}"
        if inside_roi:
            label += f" | {dwell:.1f}s"
        cv2.putText(
            frame,
            label,
            (x1, max(20, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
            lineType=cv2.LINE_AA,
        )

    active_alerts = len({(event.zone_id, event.object_id) for event in intrusion_events})
    hud_lines = [
        f"Persons in ROI: {persons_in_roi}",
        f"Active alerts: {active_alerts}",
        "Press 'q' or ESC to quit",
    ]
    y = 24
    for text in hud_lines:
        cv2.putText(
            frame,
            text,
            (10, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            lineType=cv2.LINE_AA,
        )
        y += 24


def run_monitor(camera_index: int = 0, headless: bool = False) -> None:
    from app.detection.engine import detector

    settings = get_settings()
    camera_id = f"local-camera-{camera_index}"
    camera_name = f"Local Camera {camera_index}"
    pipeline = IntrusionPipeline(camera_id=camera_id, camera_name=camera_name)
    zone_repo = RoiZoneRepository()

    cap = _open_capture(camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open camera index {camera_index}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.ROI_REFERENCE_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.ROI_REFERENCE_HEIGHT)

    last_log_time = 0.0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[WARN] Failed to read frame from camera.")
                break

            frame = cv2.resize(frame, (settings.ROI_REFERENCE_WIDTH, settings.ROI_REFERENCE_HEIGHT))
            detections = detector.detect(frame)
            result = pipeline.process(detections, frame, utc_now())
            tracked = result.tracked_objects
            intrusions = result.intrusion_events

            if headless:
                now = time.time()
                if now - last_log_time >= 1.0:
                    persons = sum(
                        1
                        for obj in tracked
                        if str(obj.get("class_label", "")) == "person"
                        and bool(obj.get("inside_roi", False))
                    )
                    active = len({(e.zone_id, e.object_id) for e in intrusions})
                    print(
                        "[INFO] "
                        f"persons_in_roi={persons} active_alerts={active} "
                        f"time={time.strftime('%H:%M:%S', time.localtime(now))}"
                    )
                    last_log_time = now
                continue

            _draw_overlay(
                frame=frame,
                camera_id=camera_id,
                zone_repo=zone_repo,
                tracked_objects=tracked,
                intrusion_events=intrusions,
            )
            cv2.imshow("Zone-Based Presence Monitor", frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):
                break
    finally:
        cap.release()
        pipeline.reset()
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass


def parse_args():
    parser = argparse.ArgumentParser(
        description="Real-time Zone-Based Human Presence Monitoring (YOLO pipeline)."
    )
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV camera index.")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without OpenCV UI window.",
    )
    parser.add_argument(
        "--capture-roi-image",
        action="store_true",
        help="Capture one frame as roi_reference.png for manual zone editing.",
    )
    parser.add_argument(
        "--write-default-zones-config",
        action="store_true",
        help="Write starter ROI config file.",
    )
    parser.add_argument(
        "--define-zone-interactive",
        action="store_true",
        help="Interactively define and append an ROI polygon.",
    )
    parser.add_argument(
        "--zones-config",
        default=None,
        help="Optional custom ROI config path.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.capture_roi_image:
        _capture_roi_image(args.camera_index)
        return
    if args.write_default_zones_config:
        path = write_default_zones_config(config_path=args.zones_config)
        print(f"[INFO] Wrote default zones config to '{path}'.")
        return
    if args.define_zone_interactive:
        _define_zone_interactive(args.camera_index, config_path=args.zones_config)
        return

    run_monitor(camera_index=args.camera_index, headless=args.headless)


if __name__ == "__main__":
    main()
