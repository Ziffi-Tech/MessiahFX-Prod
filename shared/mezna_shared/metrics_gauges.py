"""
Custom trading gauges + the pure decisions behind them.

The default metrics are HTTP-only; feed health and risk/drawdown live in Redis.
This adds Prometheus gauges (so Prometheus can alert on them) plus a no-op
fallback when prometheus_client is absent (unit tests / minimal envs). The
gauges register on the default REGISTRY, which the /metrics endpoint exposes.
"""

from __future__ import annotations


class _NoopGauge:
    """Stand-in when prometheus_client isn't installed — set() is a no-op."""
    def labels(self, *_args, **_kwargs):
        return self

    def set(self, *_args, **_kwargs):
        return None


def make_gauge(name: str, documentation: str, labelnames: tuple[str, ...] = ()):
    """Create a Prometheus Gauge, or a no-op when the lib is unavailable."""
    try:
        from prometheus_client import Gauge
        return Gauge(name, documentation, list(labelnames))
    except Exception:
        return _NoopGauge()


def drawdown_breaching(dd_pct: float, max_dd_pct: float, warn_fraction: float = 0.8) -> bool:
    """
    True when the daily drawdown has reached warn_fraction of the limit
    (e.g. 80% of a 3% cap → alert at 2.4%). Pure — drives both the gauge-side
    alert and tests.
    """
    if max_dd_pct <= 0:
        return False
    # Small epsilon so exact boundary values (e.g. 2.4 vs 3.0*0.8=2.4000…04) count.
    return dd_pct >= max_dd_pct * warn_fraction - 1e-9


def feed_up_value(heartbeat: str | None) -> int:
    """1 when a feed heartbeat is present (fresh — the key has a TTL), else 0."""
    return 1 if heartbeat else 0
