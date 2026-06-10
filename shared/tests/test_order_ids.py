"""Tests for mezna_shared.order_ids.make_client_order_id."""

from mezna_shared.order_ids import make_client_order_id


def test_deterministic_same_inputs():
    a = make_client_order_id("opp-123", 0, "buy", "BTC/USDT")
    b = make_client_order_id("opp-123", 0, "buy", "BTC/USDT")
    assert a == b


def test_differs_by_leg_side_symbol():
    base = make_client_order_id("opp-1", 0, "buy", "BTC/USDT")
    assert base != make_client_order_id("opp-1", 1, "buy", "BTC/USDT")
    assert base != make_client_order_id("opp-1", 0, "sell", "BTC/USDT")
    assert base != make_client_order_id("opp-1", 0, "buy", "ETH/USDT")
    assert base != make_client_order_id("opp-2", 0, "buy", "BTC/USDT")


def test_format_and_length():
    cid = make_client_order_id("opp-123", 0, "buy", "BTC/USDT")
    assert cid.startswith("mx-")
    assert len(cid) == 27  # "mx-" + 24 hex — within exchange clientOrderId limits


def test_missing_opportunity_id_is_random_fallback():
    a = make_client_order_id(None, 0, "buy", "BTC/USDT")
    b = make_client_order_id(None, 0, "buy", "BTC/USDT")
    assert a != b  # no stable key → random
    assert a.startswith("mx-")
