## Intruder Detection System

AI-powered multi-camera surveillance system with a FastAPI backend (YOLO + OpenVINO) and a Next.js dashboard frontend.

### Project Structure

- `backend/` – FastAPI API, YOLO/OpenVINO inference pipeline, DB, and services  
- `frontend/` – Next.js 14 dashboard UI  
- `PHASE1.md` – Quick commands to run backend and frontend together

### Prerequisites

- Python 3.10+ (for backend)
- Node.js 18+ and npm (for frontend)
- Windows 10/11 (commands below assume PowerShell)

### Backend Setup

```powershell
cd C:\dev\intruder_detection_system

# (First time) create and activate shared virtualenv, then install deps
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Run the FastAPI service (uvicorn)
cd backend\yolo_classifier
python main.py
```

The API will be available at: `http://localhost:8000/api/v1`

### Frontend Setup

```powershell
cd C:\dev\intruder_detection_system\frontend

# (First time) install dependencies
npm install

# Run the Next.js dev server
npm run dev
```

The dashboard will be available at: `http://localhost:3001`

### Environment Variables

- Backend uses `.env` in `backend/yolo_classifier` (see `app/config.py` defaults).
- Frontend can override backend URL with:

```powershell
setx NEXT_PUBLIC_API_URL "http://localhost:8000/api/v1"
```

If not set, the frontend defaults to `http://localhost:8000/api/v1`.

### Docker / Docker Compose

With Docker Desktop installed, you can run both services in containers from the project root:

```powershell
cd C:\dev\intruder_detection_system
docker compose up --build
```

This will start:

- `backend` at `http://localhost:8000`
- `frontend` at `http://localhost:3001` (configured to call `http://backend:8000/api/v1` inside the Docker network)

