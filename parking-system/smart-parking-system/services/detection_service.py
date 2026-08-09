"""
Smart Parking System — License Plate Detection Service
Cascade classifier (if available) or custom contour-based detection.
"""
import cv2
import os
import config


_cascade = None
_cascade_loaded = False


def _load_cascade():
    """Load the Haar cascade classifier once."""
    global _cascade, _cascade_loaded
    if _cascade_loaded:
        return _cascade
    _cascade_loaded = True
    
    if os.path.exists(config.CASCADE_PATH):
        _cascade = cv2.CascadeClassifier(config.CASCADE_PATH)
        if _cascade.empty():
            _cascade = None
            print("[Detection] Cascade file is empty, falling back to custom detection")
        else:
            print(f"[Detection] Loaded cascade from {config.CASCADE_PATH}")
    else:
        print("[Detection] Cascade file not found, using custom contour detection")
    return _cascade


def detect_plates_cascade(img_bgr):
    """Detect plates using Haar cascade."""
    cascade = _load_cascade()
    if cascade is None:
        return []
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    plates = cascade.detectMultiScale(gray, 1.1, 4)
    return [(x, y, w, h) for (x, y, w, h) in plates]


def detect_plates_contour(img_bgr):
    """Detect plates using edge/contour analysis."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 11, 17, 17)
    edges = cv2.Canny(gray, 30, 200)
    
    contours, _ = cv2.findContours(edges.copy(), cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:10]
    
    candidates = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.018 * perimeter, True)
        
        if len(approx) == 4:
            x, y, w, h = cv2.boundingRect(approx)
            aspect_ratio = float(w) / h if h > 0 else 0
            
            if 1.5 <= aspect_ratio <= 5.0 and w > 100 and h > 30:
                candidates.append((x, y, w, h))
    
    return candidates


def detect_plates(img_bgr):
    """
    Detect license plates in an image.
    Returns list of (x, y, w, h) tuples and the method used.
    """
    cascade = _load_cascade()
    
    if cascade is not None:
        results = detect_plates_cascade(img_bgr)
        if results:
            return results, "Cascade"
    
    results = detect_plates_contour(img_bgr)
    return results, "Contour"
