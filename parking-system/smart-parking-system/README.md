<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white" />
  <img src="https://img.shields.io/badge/OpenCV-4.x-5C3EE8?style=for-the-badge&logo=opencv&logoColor=white" />
  <img src="https://img.shields.io/badge/Ollama-AI_Chat-00C853?style=for-the-badge&logo=ollama&logoColor=white" />
</p>

# 🅿️ Smart Parking System

An AI-powered parking management system with **real-time license plate detection**, **interactive parking grid**, and a **conversational AI assistant** — all in a premium dark-themed web interface.

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🎥 **Live Detection** | Webcam-based license plate detection using Haar cascades + contour analysis |
| 🔍 **OCR Recognition** | Automatic plate text extraction via RapidOCR (no Tesseract dependency) |
| 🗺️ **Indian State Lookup** | Identifies vehicle registration state from plate prefix |
| 🅿️ **Interactive Parking Grid** | Color-coded, zone-grouped parking spaces with real-time updates |
| 🤖 **AI Assistant (ParkBot)** | Dual-mode Ollama-powered chatbot for users and admins |
| 📊 **Admin Dashboard** | Session analytics, zone occupancy bars, activity log |
| 🎬 **Simulation Mode** | Runs without a camera/video — renders a simulated parking lot |
| 🌙 **Premium UI** | Black + emerald green glassmorphic design with micro-animations |

---

## 📸 Screenshots

<details>
<summary><b>Dashboard</b></summary>
<p>Real-time overview with occupancy donut chart, activity feed, and recent detections.</p>
</details>

<details>
<summary><b>Parking Grid</b></summary>
<p>Interactive grid with Zone A/B/C grouping. Click to release occupied spaces.</p>
</details>

<details>
<summary><b>Admin Dashboard</b></summary>
<p>Session info, zone bar charts, and scrollable activity log table.</p>
</details>

<details>
<summary><b>AI Chat (ParkBot)</b></summary>
<p>Ask about free spaces in User mode, or get session reports in Admin mode.</p>
</details>

---

## 🏗️ Architecture

**Decoupled (post R17 cutover):** Flask is now a pure headless JSON + SSE + MJPEG API.
The user-facing UI lives in the `web/` subdirectory (SvelteKit  + TypeScript + Three.js).

```
smart-parking-system/
├── app.py                  # Flask app factory (headless only)
├── config.py               # Centralized configuration
├── requirements.txt        # Python dependencies
│
├── models/
│   └── database.py         # Thread-safe SQLite layer (5 tables)
│
├── services/
│   ├── ocr_service.py      # RapidOCR plate recognition
│   ├── detection_service.py# Plate detection (cascade + contour)
│   ├── parking_service.py  # Space CRUD, assignment, release, stats
│   ├── camera_service.py   # Camera/video/simulation feeds (writes runtime crops to static/images)
│   └── ai_assistant.py     # Ollama chat with live data injection
│
├── routes/
│   ├── api_routes.py       # REST + SSE endpoints (primary surface)
│   ├── feed_routes.py      # MJPEG video streams (/feed/camera, /feed/parking)
│   └── chat_routes.py      # AI chat SSE endpoint
│
├── web/                    # SvelteKit frontend (user-facing app)
│   ├── src/                # Components, routes, stores (2D/3D tower, analytics, billing, admin, chat, detect)
│   └── ... (npm run dev / build)
│
├── static/images/          # Runtime-generated plate capture PNGs (only legacy asset dir kept)
│
└── (legacy templates/ + static/css + static/js retired in R17)
```

Legacy Jinja2 + vanilla JS UI fully retired. All features now delivered by the SvelteKit app (which talks to the Flask API via Vite dev proxy in development).

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Ollama** — [Install here](https://ollama.com/download) (for AI chat)
- **Webcam** (optional — simulation mode works without one)

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/smart-parking-system.git
cd smart-parking-system

# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Setup Ollama (AI Chat)

```bash
# Pull the lightweight model (~500MB)
ollama pull qwen3:0.6b

# Ollama runs automatically in the background on most systems.
# If not, start it manually:
ollama serve
```

### Run (decoupled — post cutover)

#### 🐳 Docker (Easiest — Recommended)

```bash
# One-time build + start everything (Flask API + SvelteKit UI + Ollama)
docker compose build
docker compose up -d

# View logs
docker compose logs -f

# Stop everything later
docker compose down
```

- **UI**: Open http://localhost:3000
- **API** (for testing): http://localhost:5000
- **Ollama** (optional direct access): http://localhost:11434

The first run will automatically pull the `qwen3:0.6b` model for the AI chat. Subsequent starts are fast.

Data (SQLite DB + captured plate images) is persisted in Docker volumes.

#### Manual (without Docker)

**1. Start the headless Flask API (port 5000):**

```bash
python app.py
```

**2. Start the SvelteKit frontend (in a second terminal):**

```bash
cd web
npm install   # first time only
npm run dev
```

Open the SvelteKit dev server (usually **http://localhost:5173**). It proxies `/api/*` and `/feed/*` to the Flask backend.

The old single-page legacy UI (templates/ + static/) has been retired. The SvelteKit app in `web/` is now the complete user-facing interface (2D/3D parking tower, analytics, billing export, profiles CRUD, admin panel with token support, streaming chat, detection view, etc.).

For production: build the SvelteKit app (`npm run build` in web/, produces Node adapter output) and serve it behind a reverse proxy that forwards `/api` and `/feed` to the Flask process.

---

## 🤖 AI Assistant (ParkBot)

ParkBot uses **context injection** — before each message, live parking data is automatically prepended to the system prompt so the LLM has current awareness.

### User Mode
> "Are there free spots?" → _"There are 18 free spaces available across zones A, B, and C..."_

### Admin Mode  
> "Summarize today's activity" → _"Session started at 14:00. 12 plates detected, 8 spaces assigned, peak occupancy 67%..."_

---

## 🔌 API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stats` | GET | Parking statistics (free, occupied, occupancy %) |
| `/api/spaces` | GET | All spaces with occupancy status |
| `/api/spaces/<id>/release` | POST | Release an occupied space |
| `/api/plates` | GET | Recent plate detections |
| `/api/plates/<id>` | GET | Single plate detail |
| `/api/latest_plate` | GET | Most recently detected plate |
| `/api/activity` | GET | Activity log events |
| `/api/session` | GET | Current session info |
| `/api/chat` | POST | AI chat (SSE stream) — body: `{"message": "...", "mode": "user\|admin"}` |
| `/feed/camera` | GET | MJPEG camera stream |
| `/feed/parking` | GET | MJPEG parking lot stream |

---

## ⚙️ Configuration

All settings are in [`config.py`](config.py):

| Setting | Default | Description |
|---------|---------|-------------|
| `OLLAMA_MODEL` | `qwen3:0.6b` | Ollama model for AI chat |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `DEFAULT_SPACES` | `24` | Number of simulated parking spaces |
| `PARKING_RATE_PER_HOUR` | `20` | Rate in INR |
| `CASCADE_PATH` | `assets/indian_license_plate.xml` | Haar cascade for plate detection |
| `CARPARK_VIDEO_PATH` | `""` | Path to parking lot video (empty = simulation) |
| `ADMIN_TOKEN` | (unset) | Optional: when set, gates /api/config POST, /api/session/reset, /api/admin/connections (demo-grade; see CONTRACT in AGENT_HANDOFF) |

Override via environment variables:
```bash
OLLAMA_MODEL=llama3.2 ADMIN_TOKEN=your-secret-here FLASK_DEBUG=0 python app.py
```

---

## 🧰 Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask 3.x |
| Database | SQLite (WAL mode, thread-safe) |
| OCR | RapidOCR (ONNX Runtime) |
| Computer Vision | OpenCV 4.x |
| AI Chat | Ollama + Qwen3 0.6B |
| Frontend | SvelteKit (web/) + Svelte 5 + TypeScript + Three.js (3D tower) |
| Design | Consistent design system (tokens, components) |
| API Layer | Flask (headless JSON/SSE/MJPEG) |

---

## 📝 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

<p align="center">
  Built with ☕ and 🤖
</p>
