# Intruder Detection Backend

Backend services and tools for the computer vision intruder and ROI detection system.

## Requirements

- Python 3.10+
- PowerShell (commands below are shown for Windows)
- Hardware/runtime support needed by OpenVINO

## Project Setup

Run from repository root (shared virtualenv and requirements):

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
cd C:\dev\intruder_detection_system
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run Backend API

```powershell
cd C:\dev\intruder_detection_system
.\.venv\Scripts\Activate.ps1
cd backend\yolo_classifier
python main.py
```

Health endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health | ConvertTo-Json -Depth 10
```

## Run ROI Monitor

```powershell
.\.venv\Scripts\Activate.ps1
ids-roi-monitor --camera-index 0
```

Headless:

```powershell
ids-roi-monitor --camera-index 0 --headless
```

ROI utilities:

```powershell
ids-roi-monitor --write-default-zones-config
ids-roi-monitor --camera-index 0 --capture-roi-image
ids-roi-monitor --camera-index 0 --define-zone-interactive
```

## Live Viewer

```powershell
cd backend
python live_view.py
```

## Smoke Scripts

```powershell
cd C:\dev\intruder_detection_system
.\.venv\Scripts\Activate.ps1
python backend\yolo_classifier\tests\test_inference.py
python backend\yolo_classifier\tests\test_metrics_endpoint.py
```

## Backend Structure

- `pyproject.toml`: Python packaging metadata and dependencies
- `backend/yolo_classifier/app/`: FastAPI app, routers, services, CLI
- `backend/yolo_classifier/tests/`: backend smoke scripts
- `backend/yolo_classifier/intrusion_monitor/`: ROI configs and logs
- `backend/live_view.py`: WebSocket OpenCV viewer

## ViT Crime Classifier

A secondary classifier using `Nikeytas/google-vit-best-crime-detector` (Vision Transformer) that runs **locally** on-device. It only activates when YOLO detects a person inside an ROI zone and the dwell threshold is exceeded (intrusion event).

- **Auto-download**: Model is downloaded from HuggingFace Hub on first startup (~330MB). A flag file (`models/crime_classifier/model_downloaded.flag`) prevents re-downloading.
- **Config**: Controlled via `CRIME_CLASSIFIER_ENABLED`, `CRIME_CLASSIFIER_CONFIDENCE`, `CRIME_CLASSIFIER_DEVICE` etc. in `.env` or environment variables.
- **Output**: Binary classification — `crime` or `normal` with confidence score.
- **Alerts**: When crime is detected above the confidence threshold, a `crime_detected` alert is created and broadcast via WebSocket.

Status endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/crime-classifier/status -Headers @{Authorization="Bearer $TOKEN"} | ConvertTo-Json
```

## License

MIT. See top-level `LICENSE`.
