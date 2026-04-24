import numpy as np
from app.services.detector import detector

print("Loading OpenVINO detector...")
info = detector.get_model_info()
print(f"Device: {info['device_actual']}")
print(f"Model: {info['model']}")

# Dummy frame
frame = np.zeros((720, 1280, 3), dtype=np.uint8)

print("Running single detection...")
results = detector.detect(frame)
print(f"Detections: {len(results)}")

print("Running batched detection...")
batch_results = detector.detect_batch([frame, frame])
print(f"Batch elements: {len(batch_results)}")
for i, res in enumerate(batch_results):
    print(f"  Frame {i} detections: {len(res)}")

print("Test complete.")
