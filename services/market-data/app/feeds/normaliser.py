"""
Normalised tick format — venue-agnostic representation of a bid/ask quote.

All feeds produce NormalisedTick objects before any Redis write.
Strategy code never needs to know whether a tick came from Binance, Oanda,
or any future venue — it always reads the same structure.

Market types:
  spot   — Binance spot (BTC/USDT)
  perp   — Binance USDM perpetual (BTC/USDT:USDT)
  forex  — Oanda FX instrument (EUR_USD)
"""

from dataclasses import dataclass
from datetime import datetime

MARKET_TYPE_SPOT = "spot"
MARKET_TYPE_PERP = "perp"
MARKET_TYPE_FOREX = "forex"


@dataclass
class NormalisedTick:
    """
    A single best-bid/ask quote from any venue.

    Attributes:
        timestamp:   UTC datetime of the quote.
        venue:       "binance" | "oanda"
        symbol:      Exchange-native symbol — "BTC/USDT", "EUR_USD", etc.
        market_type: "spot" | "perp" | "forex"
        bid:         Best bid price.
        ask:         Best ask price.

    Computed properties (not stored — computed on access):
        mid:         (bid + ask) / 2
        spread_bps:  (ask - bid) / mid × 10 000 — key metric for arb edge detection.
    """

    timestamp: datetime
    venue: str
    symbol: str
    market_type: str
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        m = self.mid
        if m == 0:
            return 0.0
        return round((self.ask - self.bid) / m * 10_000, 4)

    def to_redis_hash(self) -> dict[str, str]:
        """
        Serialize to a flat string dict suitable for Redis HSET.
        All numeric values are stored as strings to avoid float precision issues.
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "venue": self.venue,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "bid": str(self.bid),
            "ask": str(self.ask),
            "mid": str(round(self.mid, 8)),
            "spread_bps": str(self.spread_bps),
        }
