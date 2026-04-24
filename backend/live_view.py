import asyncio
import base64
import json
from typing import Any, Dict, List

import cv2
import numpy as np
import websockets


WS_URL = "ws://127.0.0.1:8000/ws/global"


def _draw_detections(frame: np.ndarray, detections: List[Dict[str, Any]]) -> None:
    for det in detections:
        x = int(det.get("bbox_x", 0))
        y = int(det.get("bbox_y", 0))
        w = int(det.get("bbox_w", 0))
        h = int(det.get("bbox_h", 0))
        cls = str(det.get("class_label", ""))
        conf = float(det.get("confidence", 0.0))

        x2 = x + w
        y2 = y + h

        color = (0, 255, 0)
        if cls == "person":
            color = (0, 255, 255)
        if cls in {"car", "bus", "truck", "motorcycle", "bicycle"}:
            color = (255, 0, 0)

        cv2.rectangle(frame, (x, y), (x2, y2), color, 2)
        label = f"{cls} {conf:.2f}"
        cv2.putText(
            frame,
            label,
            (x, max(20, y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
            lineType=cv2.LINE_AA,
        )


async def run_viewer():
    print(f"[INFO] Connecting to backend websocket at {WS_URL} ...")
    async with websockets.connect(WS_URL) as ws:
        print("[INFO] Connected. Close the window or press 'q' to exit.")
        while True:
            msg = await ws.recv()
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                continue

            if payload.get("type") != "detections":
                continue

            data = payload.get("data") or {}
            b64 = data.get("frame_image") or ""
            if not b64:
                continue

            try:
                jpg_bytes = base64.b64decode(b64)
                arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
                frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            except Exception:
                continue

            detections = data.get("detections") or []
            _draw_detections(frame, detections)

            info = f"cam={data.get('camera_name','')} fps={data.get('fps',0)} tracks={len(detections)}"
            cv2.putText(
                frame,
                info,
                (10, 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
                lineType=cv2.LINE_AA,
            )

            cv2.imshow("Live Backend Feed", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q") or key == 27:
                break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    asyncio.run(run_viewer())

