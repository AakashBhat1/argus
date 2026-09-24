# Argus surveillance service. Build context: repository root.
#   docker build -f services/surveillance/Dockerfile .

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
COPY services/surveillance/pyproject.toml services/surveillance/pyproject.toml

# CPU-only torch wheels come from the PyTorch index; everything else is PyPI.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
      ./packages/argus_common ./packages/argus_vision ./services/surveillance

# ── Final image ─────────────────────────────────────────────────────────────
FROM deps AS runner

RUN addgroup --system --gid 1001 argus \
 && adduser --system --uid 1001 --ingroup argus --home /nonexistent --no-create-home argus

WORKDIR /app/services/surveillance
COPY --chown=root:root services/surveillance /app/services/surveillance

# Writable runtime state lives in mounted volumes, not the image.
RUN mkdir -p intrusion_monitor models video \
 && chown argus:argus intrusion_monitor models video \
 && rm -rf /build

USER argus

EXPOSE 8000

# X-Forwarded-* is trusted because this port is only reachable from the
# reverse proxy on the internal network (never published publicly).

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
