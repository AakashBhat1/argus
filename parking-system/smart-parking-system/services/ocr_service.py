"""
Smart Parking System — OCR Service
Singleton RapidOCR engine for license plate text recognition.
"""
import cv2
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

# Singleton engine — loaded once
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


# Indian state code mapping
STATE_CODES = {
    'AN': 'Andaman & Nicobar', 'AP': 'Andhra Pradesh', 'AR': 'Arunachal Pradesh',
    'AS': 'Assam', 'BR': 'Bihar', 'CH': 'Chandigarh', 'CT': 'Chhattisgarh',
    'DN': 'Dadra & Nagar Haveli', 'DD': 'Daman & Diu', 'DL': 'Delhi',
    'GA': 'Goa', 'GJ': 'Gujarat', 'HR': 'Haryana', 'HP': 'Himachal Pradesh',
    'JK': 'Jammu & Kashmir', 'JH': 'Jharkhand', 'KA': 'Karnataka',
    'KL': 'Kerala', 'LA': 'Ladakh', 'LD': 'Lakshadweep', 'MP': 'Madhya Pradesh',
    'MH': 'Maharashtra', 'MN': 'Manipur', 'ML': 'Meghalaya', 'MZ': 'Mizoram',
    'NL': 'Nagaland', 'OD': 'Odisha', 'PB': 'Punjab', 'PY': 'Puducherry',
    'RJ': 'Rajasthan', 'SK': 'Sikkim', 'TN': 'Tamil Nadu', 'TS': 'Telangana',
    'TR': 'Tripura', 'UP': 'Uttar Pradesh', 'UK': 'Uttarakhand', 'WB': 'West Bengal',
}


def find_state(plate_text):
    """Extract state from the first two characters of a plate."""
    if not plate_text or len(plate_text) < 2:
        return "Unknown"
    code = plate_text[:2].upper()
    return STATE_CODES.get(code, "Unknown")


def preprocess_plate_image(img_bgr):
    """Preprocess a license plate crop for better OCR accuracy."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.convertScaleAbs(gray, alpha=1.5, beta=10)
    # Resize to a standard width for consistency
    h, w = enhanced.shape[:2]
    if w > 0 and h > 0:
        new_w = 400
        new_h = int(h * new_w / w)
        enhanced = cv2.resize(enhanced, (new_w, new_h))
    return enhanced


def recognize_plate(img_bgr):
    """
    Run OCR on a license plate image crop.
    Returns: (text, confidence, state) or (None, 0, "Unknown")
    """
    engine = _get_engine()
    
    try:
        result, _elapse = engine(img_bgr)
        
        if not result:
            return None, 0, "Unknown"
        
        # Combine all detected text lines
        text = " ".join([line[1] for line in result]).strip()
        
        # Calculate average confidence
        confidence = 0.0
        try:
            confidences = [float(line[2]) for line in result]
            if confidences:
                confidence = sum(confidences) / len(confidences)
        except (IndexError, ValueError):
            pass
        
        # Clean: keep only alphanumeric + spaces
        text = ''.join(c for c in text if c.isalnum() or c.isspace()).strip()
        
        if not text:
            return None, 0, "Unknown"
        
        state = find_state(text)
        return text, round(confidence, 3), state
        
    except Exception as e:
        print(f"[OCR] Error: {e}")
        return None, 0, "Error"
