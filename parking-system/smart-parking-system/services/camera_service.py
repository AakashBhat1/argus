"""
Smart Parking System — Camera & Video Feed Service
Manages webcam capture, video file playback, and simulation mode.
"""
import cv2
import numpy as np
import time
import os
import datetime
import threading
import config
from services import detection_service, ocr_service, parking_service


_camera = None
_parking_camera = None
_stop_event = threading.Event()
_latest_plate = {"text": "", "state": "", "timestamp": "", "confidence": 0, "image": ""}


def get_latest_plate():
    """Get the most recently detected plate info."""
    return _latest_plate.copy()


def stop():
    """Signal all feeds to stop."""
    global _camera, _parking_camera
    _stop_event.set()
    if _camera is not None:
        _camera.release()
        _camera = None
    if _parking_camera is not None:
        _parking_camera.release()
        _parking_camera = None


def generate_detection_frames():
    """Generate frames from the webcam with license plate detection overlays."""
    global _camera, _latest_plate

    if _camera is None:
        _camera = cv2.VideoCapture(0)
        _camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        _camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    last_detect_time = time.time() - 10

    while not _stop_event.is_set():
        success, img = _camera.read()
        if not success:
            # If no camera, yield a placeholder frame
            img = np.zeros((480, 640, 3), dtype=np.uint8)
            img[:] = (25, 20, 15)
            cv2.putText(img, "No Camera Detected", (140, 220),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 200, 100), 2)
            cv2.putText(img, "Connect a webcam to enable detection", (100, 270),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 120, 120), 1)
            ret, buffer = cv2.imencode('.jpg', img)
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            time.sleep(1)
            continue

        plates, method = detection_service.detect_plates(img)

        cv2.putText(img, f"Detection: {method}", (10, 460),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 100), 1)

        current_time = time.time()
        for (x, y, w, h) in plates:
            area = w * h
            if area > config.MIN_PLATE_AREA:
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 200, 100), 2)
                cv2.putText(img, "Plate", (x, y - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 100), 2)

                if current_time - last_detect_time > 5:
                    roi = img[y:y + h, x:x + w]
                    text, confidence, state = ocr_service.recognize_plate(roi)

                    if text:
                        # Save plate image
                        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
                        img_path = f"static/images/plate_{ts}.png"
                        os.makedirs("static/images", exist_ok=True)
                        processed = ocr_service.preprocess_plate_image(roi)
                        cv2.imwrite(img_path, processed)

                        # Record in DB
                        parking_service.record_detection(text, state, confidence)
                        space_id = parking_service.assign_space(text)

                        _latest_plate = {
                            "text": text, "state": state,
                            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "confidence": round(confidence * 100, 1),
                            "image": img_path,
                            "space": space_id,
                        }

                        cv2.putText(img, f"Plate: {text}", (10, 350),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 100), 2)
                        cv2.putText(img, f"State: {state}", (10, 380),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 100), 2)
                        if space_id:
                            cv2.putText(img, f"Space: {space_id}", (10, 410),
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 100), 2)
                        last_detect_time = current_time

        ret, buffer = cv2.imencode('.jpg', img)
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')


def generate_parking_frames():
    """Generate parking lot visualization frames (video or simulation)."""
    global _parking_camera

    use_video = config.CARPARK_VIDEO_PATH and os.path.exists(config.CARPARK_VIDEO_PATH)

    if use_video and _parking_camera is None:
        _parking_camera = cv2.VideoCapture(config.CARPARK_VIDEO_PATH)

    while not _stop_event.is_set():
        if use_video:
            yield from _video_frame()
        else:
            yield from _simulation_frame()
            time.sleep(0.2)


def _simulation_frame():
    """Render a simulated parking lot grid."""
    from services import parking_service

    img = np.zeros((500, 850, 3), dtype=np.uint8)
    img[:] = (20, 18, 15)

    spaces = parking_service.get_all_spaces()
    stats = parking_service.get_stats()
    
    if not spaces:
        cv2.putText(img, "No Spaces Configured", (250, 250),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 200, 100), 2)
        ret, buf = cv2.imencode('.jpg', img)
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
        return

    # Layout parameters
    cols = 8
    cell_w, cell_h = 90, 40
    pad_x, pad_y = 30, 80
    gap_x, gap_y = 10, 12

    # Header
    cv2.putText(img, f"Free: {stats['free']}/{stats['total']}", (30, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 100), 2)
    cv2.putText(img, "SIMULATION MODE", (600, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 168, 200), 1)

    for i, space in enumerate(spaces):
        r = i // cols
        c = i % cols
        x = pad_x + c * (cell_w + gap_x)
        y = pad_y + r * (cell_h + gap_y)

        occupied = space["is_occupied"]

        if occupied:
            cv2.rectangle(img, (x, y), (x + cell_w, y + cell_h), (40, 30, 25), -1)
            cv2.rectangle(img, (x, y), (x + cell_w, y + cell_h), (0, 0, 180), 1)
            cv2.putText(img, space["space_id"], (x + 5, y + 16),
                        cv2.FONT_HERSHEY_PLAIN, 0.85, (200, 200, 200), 1)
            if space["plate_text"]:
                txt = space["plate_text"][-8:]
                cv2.putText(img, txt, (x + 5, y + 32),
                            cv2.FONT_HERSHEY_PLAIN, 0.7, (0, 220, 220), 1)
        else:
            cv2.rectangle(img, (x, y), (x + cell_w, y + cell_h), (0, 140, 70), 1)
            cv2.putText(img, space["space_id"], (x + 5, y + 16),
                        cv2.FONT_HERSHEY_PLAIN, 0.85, (0, 200, 100), 1)
            cv2.putText(img, "FREE", (x + 25, y + 32),
                        cv2.FONT_HERSHEY_PLAIN, 0.7, (0, 140, 70), 1)

    # Draw zone dividers
    rows_total = (len(spaces) + cols - 1) // cols
    for r in range(1, rows_total):
        line_y = pad_y + r * (cell_h + gap_y) - gap_y // 2
        cv2.line(img, (pad_x, line_y), (pad_x + cols * (cell_w + gap_x) - gap_x, line_y),
                 (40, 40, 40), 1)

    # Recent plate footer
    recent = parking_service.get_recent_plates(1)
    if recent:
        p = recent[0]
        cv2.putText(img, f"Latest: {p['plate_text']} ({p['state']})", (30, 475),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1)

    ret, buf = cv2.imencode('.jpg', img)
    yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')


def _video_frame():
    """Read and process a frame from the parking lot video."""
    global _parking_camera
    if _parking_camera is None:
        return

    success, img = _parking_camera.read()
    if not success:
        _parking_camera.set(cv2.CAP_PROP_POS_FRAMES, 0)
        return

    ret, buf = cv2.imencode('.jpg', img)
    if ret:
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
