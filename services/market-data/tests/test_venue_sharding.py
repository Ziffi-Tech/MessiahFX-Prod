"""Tests for FEED_VENUES venue sharding (horizontal feed scaling)."""

from app.config import Settings


def _settings(**kw) -> Settings:
    return Settings(DATABASE_URL="postgresql+asyncpg://t:t@localhost/t", **kw)


def test_empty_allowlist_runs_everything():
    s = _settings(FEED_VENUES="")
    for venue in ("binance", "oanda", "bybit", "okx", "kraken"):
        assert s.venue_enabled(venue)


def test_allowlist_filters_venues():
    s = _settings(FEED_VENUES="binance, oanda")
    assert s.venue_enabled("binance")
    assert s.venue_enabled("OANDA")          # case-insensitive
    assert not s.venue_enabled("bybit")
    assert not s.venue_enabled("kraken")


def test_bar_writer_targets_respect_shard():
    s = _settings(
        FEED_VENUES="oanda",
        BINANCE_SPOT_SYMBOLS="BTC/USDT",
        BINANCE_PERP_SYMBOLS="",
        OANDA_INSTRUMENTS="EUR_USD",
    )
    venues = {v for v, _ in s.bar_writer_targets}
    assert venues == {"oanda"}


def test_orderbook_targets_respect_shard():
    s = _settings(
        FEED_VENUES="binance",
        ORDERBOOK_SYMBOLS="binance:BTC/USDT,bybit:BTC/USDT:USDT",
    )
    assert s.orderbook_targets == [("binance", "BTC/USDT")]
