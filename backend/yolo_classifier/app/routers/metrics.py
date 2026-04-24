"""
Metrics API Router
===================

Exposes inference pipeline metrics for monitoring dashboards and
Prometheus scraping.

Endpoints:
  GET /api/v1/metrics            → JSON metrics
  GET /api/v1/metrics/prometheus  → Prometheus text format
  GET /api/v1/metrics/model       → Model info and device details
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.detection import detector
from app.services.metrics import inference_metrics

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/")
async def get_metrics():
    """
    Return all inference metrics as JSON.

    Includes throughput, latency breakdowns, batching stats,
    queue depth, and per-camera FPS.
    """
    return inference_metrics.get_metrics()


@router.get("/prometheus", response_class=PlainTextResponse)
async def get_metrics_prometheus():
    """
    Return metrics in Prometheus exposition format.

    Suitable for scraping by Prometheus, Grafana Agent, or
    any OpenMetrics-compatible collector.
    """
    return inference_metrics.format_prometheus()


@router.get("/model")
async def get_model_info():
    """
    Return the loaded model's metadata and runtime configuration.

    Includes device info, class names, input shape, and
    cumulative inference statistics.
    """
    return detector.get_model_info()
