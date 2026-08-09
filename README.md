# Argus

Self-hosted, real-time multi-camera surveillance with YOLO/OpenVINO detection, region-of-interest alerts, and a Next.js operations dashboard.

[Features](#features) · [Architecture](#architecture) · [Getting started](#getting-started) · [Configuration](#configuration) · [Contributing](#contributing)

> [!NOTE]
> Argus is an active prototype. It requires a compatible OpenVINO model and camera or video sources that you provide; model weights are not stored in this repository.

## Features

- Runs multi-camera object detection through a batched OpenVINO inference pipeline.
- Tracks people and evaluates dwell time inside configurable regions of interest.
- Delivers live detections and alerts over authenticated WebSocket channels.
- Streams browser-compatible live video through MediaMTX and WebRTC.
- Provides camera management, alert review, analytics, and inference metrics in a responsive dashboard.
- Supports a local ViT secondary classifier and an optional Roboflow classifier for detection enrichment.
- Includes tenant-aware API authorization, stream URL validation, retention controls, and PostgreSQL or SQLite persistence.

## Architecture

| Component | Technology | Responsibility |
| --- | --- | --- |
| API and inference | FastAPI, OpenVINO, OpenCV | Camera lifecycle, detection, tracking, ROI events, alerts, and analytics |
| Dashboard | Next.js, React, Recharts | Live operations, camera configuration, alert review, and metrics |
| Media layer | MediaMTX, WebRTC, RTSP | Camera ingest and low-latency browser playback |
| Data layer | PostgreSQL or SQLite, SQLAlchemy | Users, cameras, detections, alerts, and analytics |
| Edge proxy | Nginx, Certbot | HTTP routing, WebSocket proxying, and optional TLS termination |

The FastAPI service receives camera streams, queues frames for OpenVINO inference, tracks detected objects, evaluates ROI rules, and broadcasts resulting events to the dashboard. MediaMTX handles the video path separately so browsers can play live feeds without routing video frames through the API.

## Requirements

- Python 3.10+
- Node.js 20 and npm
- An OpenVINO IR model (`.xml` and matching `.bin` files)
- A local camera, video file, or reachable stream source
- Docker Desktop and Docker Compose for the containerized stack

## Getting started

### 1. Clone and configure the backend

```powershell
git clone https://github.com/AakashBhat1/argus.git
cd argus

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item backend\yolo_classifier\.env.example backend\yolo_classifier\.env
```

Place your OpenVINO model files in `backend/yolo_classifier/models/`. The default configuration expects:

```text
backend/yolo_classifier/models/yolo26n_int8.xml
backend/yolo_classifier/models/yolo26n_int8.bin
```

Change `OPENVINO_MODEL_PATH` in `backend/yolo_classifier/.env` if your model uses a different path or filename.

### 2. Start the API

```powershell
cd backend\yolo_classifier
python main.py
```

The API starts at `http://localhost:8000`; its health endpoint is `http://localhost:8000/api/v1/health`.

### 3. Start the dashboard

In a second PowerShell window:

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

Open `http://localhost:3001`.

## Docker deployment

The included Compose stack runs PostgreSQL, the FastAPI backend, the Next.js dashboard, MediaMTX, Nginx, and an on-demand Certbot service. Start by copying the example environment files:

```powershell
Copy-Item .env.example .env
Copy-Item backend\yolo_classifier\.env.example backend\yolo_classifier\.env
```

Replace every placeholder secret, then provision the configured OpenVINO `.xml` and `.bin` files in the Compose `models_data` volume. How the model volume is populated depends on the deployment environment; the backend cannot start with an empty model volume.

Once the secrets and model volume are ready:

```powershell
docker compose up --build
```

See the [deployment roadmap](plans/deployment-roadmap.md) and [AWS security checklist](plans/aws-deployment-security-checklist.md) for the repository's deployment notes.

## Configuration

The checked-in example files document the available settings:

- [Compose and media secrets](.env.example)
- [Backend database, inference, stream, and retention settings](backend/yolo_classifier/.env.example)
- [Frontend API, WebSocket, and MediaMTX URLs](frontend/.env.example)

Keep real credentials in ignored `.env` files. Do not commit model weights, camera credentials, tokens, or production endpoints.

## Validation

Run backend tests from the repository root:

```powershell
python -m pytest backend\yolo_classifier\tests
```

Run the frontend linter from `frontend/`:

```powershell
npm run lint
```

## Project structure

```text
argus/
├── backend/yolo_classifier/   FastAPI application, inference, and tests
├── frontend/                  Next.js dashboard
├── nginx/                     Reverse proxy and TLS configuration
├── plans/                     Deployment and security notes
├── scripts/                   Secret generation, relay, and TLS helpers
├── docker-compose.yml         Containerized deployment stack
└── mediamtx.yml               MediaMTX configuration
```

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Bug reports and feature requests can be submitted through the repository's issue templates.

For vulnerabilities, follow [SECURITY.md](SECURITY.md) and avoid disclosing sensitive details in a public issue.

## License

This repository does not currently contain a valid software license. Unless and until the maintainer adds one, no permission is granted to copy, modify, or distribute the project.
