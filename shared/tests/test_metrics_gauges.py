"""Tests for the pure metric/alert decisions in mezna_shared.metrics_gauges."""

from mezna_shared.metrics_gauges import drawdown_breaching, feed_up_value, make_gauge


def test_drawdown_breaching():
    # 80% of a 3% cap = 2.4%
    assert drawdown_breaching(2.4, 3.0) is True
    assert drawdown_breaching(2.5, 3.0) is True
    assert drawdown_breaching(2.0, 3.0) is False


def test_drawdown_custom_fraction():
    assert drawdown_breaching(1.5, 3.0, warn_fraction=0.5) is True
    assert drawdown_breaching(1.4, 3.0, warn_fraction=0.5) is False


def test_drawdown_no_limit_never_breaches():
    assert drawdown_breaching(99.0, 0.0) is False


def test_feed_up_value():
    assert feed_up_value("2026-06-10T00:00:00Z") == 1
    assert feed_up_value(None) == 0
    assert feed_up_value("") == 0


def test_make_gauge_noop_is_safe():
    # Whether or not prometheus_client is installed, set()/labels() must not raise.
    g = make_gauge("mezna_test_gauge_xyz", "doc", ("venue",))
    g.labels(venue="binance").set(1)
    g2 = make_gauge("mezna_test_gauge_plain", "doc")
    g2.set(3.14)
