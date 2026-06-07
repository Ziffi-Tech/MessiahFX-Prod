"""Tests for symbol→venue routing (mezna_shared.venues)."""

from mezna_shared.venues import parse_symbol_spec, infer_venue, KNOWN_VENUES


def test_plain_symbols_keep_legacy_inference():
    assert parse_symbol_spec("BTC/USDT") == ("binance", "BTC/USDT")
    assert parse_symbol_spec("ETH/USDT") == ("binance", "ETH/USDT")
    assert parse_symbol_spec("EUR_USD") == ("oanda", "EUR_USD")
    assert parse_symbol_spec("BTCUSDT") == ("binance", "BTCUSDT")


def test_explicit_venue_prefix():
    assert parse_symbol_spec("bybit:BTC/USDT:USDT") == ("bybit", "BTC/USDT:USDT")
    assert parse_symbol_spec("okx:ETH/USDT:USDT") == ("okx", "ETH/USDT:USDT")
    assert parse_symbol_spec("oanda:EUR_USD") == ("oanda", "EUR_USD")
    assert parse_symbol_spec("binance:BTC/USDT") == ("binance", "BTC/USDT")


def test_ccxt_colon_symbol_without_prefix_is_not_mistaken_for_venue():
    # "BTC/USDT:USDT" — segment before ':' is "BTC/USDT", not a known venue.
    assert parse_symbol_spec("BTC/USDT:USDT") == ("binance", "BTC/USDT:USDT")


def test_whitespace_and_case():
    assert parse_symbol_spec("  bybit:BTC/USDT  ") == ("bybit", "BTC/USDT")
    assert parse_symbol_spec("BYBIT:BTC/USDT") == ("bybit", "BTC/USDT")


def test_infer_and_known_venues():
    assert infer_venue("EUR_USD") == "oanda"
    assert infer_venue("SOL/USDT") == "binance"
    assert "bybit" in KNOWN_VENUES and "okx" in KNOWN_VENUES
