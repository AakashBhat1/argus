FROM python:3.10-slim AS base

# System libraries required by OpenCV and OpenVINO
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
 && rm -rf /var/lib/apt/lists/*

# ── Dependencies layer ──────────────────────────────────────────────────────
FROM base AS deps

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ── Final image ─────────────────────────────────────────────────────────────
FROM deps AS runner

WORKDIR /app

# Non-root user for security
RUN addgroup --system --gid 1001 appgroup \
 && adduser  --system --uid 1001 --ingroup appgroup appuser

# Copy backend source (build context is project root)
COPY backend/yolo_classifier /app/backend/yolo_classifier

# Ensure the intrusion_monitor log directory exists
RUN mkdir -p /app/backend/yolo_classifier/intrusion_monitor \
 && chown -R appuser:appgroup /app

USER appuser

WORKDIR /app/backend/yolo_classifier

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
