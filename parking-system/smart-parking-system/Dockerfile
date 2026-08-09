# Backend Dockerfile for Smart Parking System (Flask headless API)
FROM python:3.12-slim

# System dependencies for OpenCV headless + ONNX Runtime
# libgl1 + libglib2.0-0: required by opencv-python-headless even without display
# No X11/libsm6/libxrender needed for headless build
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Runtime directories:
#   data/        - SQLite database (mounted as volume in compose)
#   static/images - plate crop images (mounted as volume in compose)
#   assets/      - optional Haar cascade XML (baked into image or user-supplied)
RUN mkdir -p data static/images assets

ENV PYTHONUNBUFFERED=1
ENV FLASK_DEBUG=0
ENV HOST=0.0.0.0
ENV PORT=5000
ENV OLLAMA_BASE_URL=http://ollama:11434
ENV DATABASE_PATH=/app/data/parking_system.db

EXPOSE 5000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/health')" || exit 1

CMD ["python", "app.py"]
