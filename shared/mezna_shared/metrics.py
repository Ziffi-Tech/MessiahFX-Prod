"""
Prometheus metrics setup — shared helper for all FastAPI services.

Usage in any service main.py (after app = FastAPI(...)):

    from mezna_shared.metrics import setup_metrics
    setup_metrics(app)

This:
  1. Instruments all FastAPI routes automatically (request count, latency, size).
  2. Exposes a /metrics endpoint for Prometheus to scrape.
  3. Adds custom trading-specific gauges exposed by services that set them.

The instrumentator uses the `prometheus-fastapi-instrumentator` library.
Prometheus scrapes /metrics every 15 seconds (see infrastructure/prometheus/).
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI

log = structlog.get_logger()


def setup_metrics(app: FastAPI, service_name: str = "") -> None:
    """
    Instrument a FastAPI app with Prometheus metrics and expose /metrics.

    Call this once per service, after the app object is created and routes
    are registered. The /metrics endpoint is always enabled (not gated by DEBUG).

    Args:
        app:          The FastAPI application instance.
        service_name: Optional label added to all metrics for this service.
                      Defaults to SERVICE_NAME from env if not provided.
    """
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        label = service_name or _get_service_name()

        instrumentator = Instrumentator(
            # Always instrument regardless of path or method
            should_group_status_codes=True,
            should_ignore_untemplated=True,
            should_respect_env_var=False,
            should_instrument_requests_inprogress=True,
            excluded_handlers=["/metrics"],       # don't track /metrics itself
            inprogress_name="http_requests_inprogress",
            inprogress_labels=True,
        )

        instrumentator.instrument(app)
        instrumentator.expose(app, endpoint="/metrics", include_in_schema=False)

        log.info(
            "metrics.setup_complete",
            service=label,
            endpoint="/metrics",
            note="Prometheus scraping enabled",
        )

    except ImportError:
        # prometheus-fastapi-instrumentator not installed — skip silently.
        # This allows the shared lib to be used without the optional dependency
        # during unit tests or standalone development.
        log.warning(
            "metrics.not_available",
            hint="Install prometheus-fastapi-instrumentator to enable /metrics",
        )
    except Exception as exc:
        # Never crash the service because metrics failed to register.
        log.error("metrics.setup_failed", error=str(exc))


def _get_service_name() -> str:
    """Read SERVICE_NAME from environment, fall back to 'unknown'."""
    import os
    return os.getenv("SERVICE_NAME", "unknown")
