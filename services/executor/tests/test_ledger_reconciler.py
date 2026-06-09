"""Tests for the executor ledger-reconciliation orchestration (injectable fetcher)."""

import asyncio

from app.ledger_reconciler import reconcile_ledger, live_venues


async def _fetch_empty(_venue):
    return []


def test_paper_mode_skipped():
    r = asyncio.run(reconcile_ledger([], _fetch_empty, ["binance"], paper_mode=True))
    assert r["status"] == "skipped"
    assert r["ok"] is True
    assert "paper" in r["reason"]


def test_no_live_venues_skipped():
    r = asyncio.run(reconcile_ledger([], _fetch_empty, [], paper_mode=False))
    assert r["status"] == "skipped"
    assert "no live venues" in r["reason"]


def test_live_matched_ok():
    ours = [{"venue": "binance", "symbol": "BTC/USDT", "net_qty": 1.0, "avg_price": 100.0}]

    async def fetch(_venue):
        return [{"venue": "binance", "symbol": "BTC/USDT", "qty": 1.0, "avg_price": 100.0}]

    r = asyncio.run(reconcile_ledger(ours, fetch, ["binance"], paper_mode=False))
    assert r["status"] == "ok"
    assert r["ok"] is True
    assert r["summary"]["matched"] == 1


def test_live_drift_flagged():
    ours = [{"venue": "binance", "symbol": "BTC/USDT", "net_qty": 1.0, "avg_price": 100.0}]

    async def fetch(_venue):
        return [{"venue": "binance", "symbol": "BTC/USDT", "qty": 0.4, "avg_price": 100.0}]

    r = asyncio.run(reconcile_ledger(ours, fetch, ["binance"], paper_mode=False))
    assert r["ok"] is False
    assert r["summary"]["drifted"] == 1


def test_fetch_error_captured_not_fatal():
    async def boom(_venue):
        raise RuntimeError("exchange down")

    r = asyncio.run(reconcile_ledger([], boom, ["binance"], paper_mode=False))
    assert r["status"] == "ok"            # still returns a report
    assert "binance" in r["fetch_errors"]


def test_exchange_only_position_is_drift():
    # We track nothing, but the exchange reports an open position — must flag.
    async def fetch(_venue):
        return [{"venue": "bybit", "symbol": "ETH/USDT:USDT", "qty": 3.0, "avg_price": 2000.0}]

    r = asyncio.run(reconcile_ledger([], fetch, ["bybit"], paper_mode=False))
    assert r["ok"] is False
    assert r["summary"]["exch_only"] == 1


class _Settings:
    BINANCE_API_KEY = "k"
    BYBIT_API_KEY = ""
    OKX_API_KEY = "k"; OKX_API_PASSWORD = ""   # incomplete → excluded
    KRAKEN_API_KEY = "k"


def test_live_venues_from_credentials():
    assert live_venues(_Settings()) == ["binance", "kraken"]
