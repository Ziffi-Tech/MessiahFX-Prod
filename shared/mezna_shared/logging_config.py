"""
Structured logging configuration using structlog.

All services call setup_logging() at startup. Output is JSON in production
and human-readable in development (DEBUG=true).

Usage:
    from mezna_shared.logging_config import setup_logging
    import structlog

    setup_logging(service_name="gateway", log_level="INFO")
    log = structlog.get_logger()
    log.info("order.submitted", order_id="abc123", venue="binance")
"""

import logging
import sys
import structlog
from typing import Any


def setup_logging(
    service_name: str,
    log_level: str = "INFO",
    debug: bool = False,
) -> None:
    """
    Configure structlog for JSON output (production) or pretty output (dev).

    In production (debug=False): emits newline-delimited JSON to stdout.
    In development (debug=True): emits coloured, human-readable output.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Shared processors applied to every log event
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        # add_logger_name omitted — incompatible with PrintLoggerFactory (.name missing)
        # service name is injected by _inject_service_name processor below instead
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        # Inject service name into every log event
        _inject_service_name(service_name),
    ]

    if debug:
        # Human-readable coloured output for local development
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # JSON for production (Loki-compatible)
        processors = [
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib logging so third-party libraries log through structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )


def _inject_service_name(service_name: str) -> Any:
    """Processor that injects the service name into every log event."""

    def processor(
        logger: Any, method: str, event_dict: dict[str, Any]
    ) -> dict[str, Any]:
        event_dict["service"] = service_name
        return event_dict

    return processor


def get_request_logger(correlation_id: str | None = None) -> Any:
    """
    Return a logger with a correlation ID bound for request tracing.
    Call this at the start of each HTTP request handler.
    """
    log = structlog.get_logger()
    if correlation_id:
        return log.bind(correlation_id=correlation_id)
    return log
