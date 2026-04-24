FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/models/embedding

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 \
    libgomp1 \
    curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

RUN addgroup --system --gid 1001 appgroup \
 && adduser --system --uid 1001 --ingroup appgroup appuser

COPY project /app/project
COPY chatbot_service.py run_ingestion.py /app/

RUN mkdir -p /app/project/data /app/models/embedding \
 && chown -R appuser:appgroup /app

VOLUME ["/app/project/data", "/app/models/embedding"]

USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
 CMD curl -f http://127.0.0.1:8001/health || exit 1

CMD ["python","-m","uvicorn","chatbot_service:app","--host","0.0.0.0","--port","8001"]