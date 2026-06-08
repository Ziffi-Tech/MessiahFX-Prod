"""Tests for opportunity upsert param mapping (mezna_shared.opportunities)."""

import json

from mezna_shared.opportunities import opportunity_upsert_params


def _full_payload():
    # Shape an opportunity gets by the time risk persists it: detected + ai + risk.
    return {
        "strategy_type": "swing", "venue": "binance", "source": "tradingview",
        "symbol_primary": "BTC/USDT", "symbol_secondary": None,
        "detected_at": "2026-06-08T00:00:00+00:00", "latency_profile": "standard",
        "spread": "1.5", "net_edge_bps": "5.0", "fee_cost_bps": "2.0",
        "expected_return_bps": "7.0", "paper_mode": True,
        "ai_score": 80, "ai_reason": "looks good", "ai_timeout": False,
        "ai_scored_at": "2026-06-08T00:00:01+00:00",
        "risk_approved": True, "risk_checked_at": "2026-06-08T00:00:02+00:00",
        "raw_signal": {"trigger": "tradingview", "direction": "long"},
    }


def test_maps_core_and_enrichment_fields():
    p = opportunity_upsert_params("11111111-1111-1111-1111-111111111111", _full_payload())
    assert p["id"] == "11111111-1111-1111-1111-111111111111"
    assert p["strategy_type"] == "swing" and p["venue"] == "binance"
    assert p["symbol_primary"] == "BTC/USDT" and p["symbol_secondary"] is None
    assert p["net_edge_bps"] == 5.0 and p["spread"] == 1.5      # coerced to float
    assert p["ai_score"] == 80
    assert p["ai_reasoning"] == "looks good"                     # ai_reason -> ai_reasoning
    assert p["ai_timeout"] is False
    assert p["risk_approved"] is True
    assert json.loads(p["raw_signal"]) == {"trigger": "tradingview", "direction": "long"}


def test_ai_timeout_string_coerced_to_bool():
    assert opportunity_upsert_params("x", {"ai_timeout": "true"})["ai_timeout"] is True
    assert opportunity_upsert_params("x", {"ai_timeout": "false"})["ai_timeout"] is False


def test_defaults_for_missing_keys():
    p = opportunity_upsert_params("x", {})
    assert p["strategy_type"] == "unknown" and p["venue"] == "unknown"
    assert p["source"] == "internal" and p["latency_profile"] == "standard"
    assert p["paper_mode"] is True
    assert p["net_edge_bps"] is None and p["spread"] is None
    assert p["ai_score"] is None and p["risk_approved"] is None
    assert p["raw_signal"] == "{}"


def test_ai_reasoning_fallback_key():
    # Some payloads may carry ai_reasoning directly instead of ai_reason.
    assert opportunity_upsert_params("x", {"ai_reasoning": "direct"})["ai_reasoning"] == "direct"
    # Bad numeric strings coerce to None, not crash.
    assert opportunity_upsert_params("x", {"net_edge_bps": "n/a"})["net_edge_bps"] is None
