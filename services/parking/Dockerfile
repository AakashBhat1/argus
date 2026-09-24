# Argus parking service. Build context: repository root.
#   docker build -f services/parking/Dockerfile .

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime libraries required by OpenCV and OpenVINO.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# ── Dependencies layer ──────────────────────────────────────────────────────
FROM base AS deps

WORKDIR /build
COPY packages/argus_common packages/argus_common
COPY packages/argus_vision packages/argus_vision
COPY services/parking/pyproject.toml services/parking/pyproject.toml

# CPU-only torch wheels come from the PyTorch index; everything else is PyPI.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
      ./packages/argus_common ./packages/argus_vision ./services/parking

# ── Final image ─────────────────────────────────────────────────────────────
FROM deps AS runner

RUN addgroup --system --gid 1001 argus \
 && adduser --system --uid 1001 --ingroup argus --home /nonexistent --no-create-home argus

WORKDIR /app/services/parking
COPY --chown=root:root services/parking /app/services/parking

# Writable runtime state lives in mounted volumes, not the image.
RUN mkdir -p models video data \
 && chown argus:argus models video data \
 && rm -rf /build

USER argus

EXPOSE 8100

# X-Forwarded-* is trusted because this port is only reachable from the
# reverse proxy on the internal network (never published publicly).

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/api/v1/parking/health')" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--proxy-headers", "--forwarded-allow-ips", "*"]
