"""The gateway proxy must front every internal service the dashboard calls."""

from app.routes.proxy import _UPSTREAM

EXPECTED = ("journal", "risk", "strategy", "backtest", "ai", "market-data", "executor", "rag")


def test_upstream_has_all_services():
    for svc in EXPECTED:
        assert svc in _UPSTREAM, f"proxy missing upstream for {svc!r}"
        assert _UPSTREAM[svc].startswith("http"), svc


def test_rag_upstream_points_at_rag():
    assert "rag" in _UPSTREAM["rag"]
