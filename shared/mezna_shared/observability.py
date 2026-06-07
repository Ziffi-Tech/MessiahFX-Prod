"""
Error tracking / observability bootstrap (Sentry).

init_sentry() is called once per service at startup, right after setup_logging().
It is a safe no-op when SENTRY_DSN is unset (local/dev, paper without a project),
so wiring it in changes nothing until an operator provides a DSN.

Config (all via env, so no per-service Settings changes are required):
  SENTRY_DSN                   enable when set; no-op when empty
  SENTRY_ENVIRONMENT           defaults to TRADING_MODE, else "production"
  SENTRY_TRACES_SAMPLE_RATE    APM sampling 0.0–1.0 (default 0.0 = errors only)
  SENTRY_RELEASE               optional release/version tag

Sentry auto-enables its FastAPI/Starlette/logging integrations when those
packages are importable, so no framework wiring is needed here.
"""

import os

import structlog

log = structlog.get_logger()


def init_sentry(
    service_name: str,
    *,
    dsn: str | None = None,
    environment: str | None = None,
    traces_sample_rate: float | None = None,
    release: str | None = None,
) -> bool:
    """
    Initialise Sentry for a service. Returns True if enabled, False if skipped.

    Never raises — observability setup must not block a service from starting.
    """
    dsn = (dsn or os.getenv("SENTRY_DSN", "")).strip()
    if not dsn:
        log.info("sentry.disabled", service=service_name, reason="SENTRY_DSN not set")
        return False

    try:
        import sentry_sdk
    except Exception as exc:  # dependency missing — degrade gracefully
        log.warning("sentry.unavailable", service=service_name, error=str(exc))
        return False

    env = environment or os.getenv("SENTRY_ENVIRONMENT") or os.getenv("TRADING_MODE", "production")
    try:
        rate = float(
            traces_sample_rate
            if traces_sample_rate is not None
            else os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")
        )
    except (TypeError, ValueError):
        rate = 0.0

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            release=release or os.getenv("SENTRY_RELEASE") or None,
            traces_sample_rate=rate,
            send_default_pii=False,  # never ship PII / credentials to Sentry
        )
        sentry_sdk.set_tag("service", service_name)
    except Exception as exc:
        log.warning("sentry.init_failed", service=service_name, error=str(exc))
        return False

    log.info(
        "sentry.enabled",
        service=service_name,
        environment=env,
        traces_sample_rate=rate,
    )
    return True
