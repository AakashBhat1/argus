# Argus

Self-hosted, real-time multi-camera surveillance with YOLO/OpenVINO detection, region-of-interest alerts, and a Next.js operations dashboard.

[Features](#features) · [Architecture](#architecture) · [Getting started](#getting-started) · [Configuration](#configuration) · [Contributing](#contributing)

> [!NOTE]
> Argus is an active prototype. It requires a compatible OpenVINO model and camera or video sources that you provide; model weights are not stored in this repository.

## Features

- Runs multi-camera object detection through a batched OpenVINO inference pipeline.
- Tracks people and evaluates dwell time inside configurable regions of interest, anchored on the foot point and resilient to short detector dropouts and track-ID switches.
- Scores every tracked person with a contextual risk engine (zone type, arming schedule, dwell, origin, behaviour, time of day, group contacts, plate/manual authorization) and raises one alert per incident instead of per frame.
- Estimates metric distance and ground position per detection from a pinhole camera model, optionally refined with a four-point ground-plane calibration.
- Ships a Security Console: arm/disarm, expected-visitor and vehicle grants, live risk timeline, and an escalation feed.
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

## Demo walkthrough: contextual intrusion

1. **Cameras → Zones**: draw a polygon, pick a *zone type* (`restricted`, `perimeter`, `entrance`, `driveway`, `parking`, `public`) and an *arming schedule* (always, never, or time windows). Public zones never alert; restricted zones alert on dwell.
2. **Cameras → Calibration**: set the horizontal FOV, or click the four corners of a known rectangle on the ground (e.g. a parking bay) and enter its size. Distance labels on the live feed switch from `pinhole` to `metric`.
3. **Security Console**: set the site to `armed`, `auto` (follow zone schedules) or `disarmed`. Grant an expected visitor, or simulate a gate plate read for an authorized or blacklisted vehicle.
4. **Live feed**: each person shows distance, risk score and level. A person who steps out of an authorized car, or who the operator marks as known, is scored `authorized` and never alerts. A stranger dwelling in an armed restricted zone becomes an incident once; a blacklisted plate, a crime-classifier hit, or a prolonged close contact between two people adds to the score and pushes it toward `critical`.
5. **Escalation feed / Alerts**: only `alert` and `critical` transitions are persisted, with the human-readable reasons that produced the score.

The ROI dwell threshold, grace period, score thresholds, quiet hours and close-contact rules are all tunable in the backend `.env` (see the "Contextual intrusion / risk engine" block in [the example file](backend/yolo_classifier/.env.example)).

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
