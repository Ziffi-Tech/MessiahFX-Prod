"""
OHLCVBar model — persisted historical candles per (venue, symbol, interval).

One row per completed candle. Two producers write it (last-writer-wins on the
natural key):
  * market-data live bar writer — resamples the Redis tick cache (volume = tick
    count, a liquidity proxy for quote feeds), source='live_ticks';
  * ccxt OHLCV backfill — exchange REST history (real traded volume),
    source='exchange_rest'.

The backtest service reads ranges from here; see mezna_shared.ohlcv for the
upsert/read helpers and migrations/versions/004_ohlcv_bars.py for the schema.

Natural composite PK (venue, symbol, interval, bucket_start) — no surrogate id,
since the key is the dedup target on a high-row-count timeseries table.
"""

from datetime import datetime

from sqlalchemy import TIMESTAMP, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class OHLCVBar(Base):
    __tablename__ = "ohlcv_bars"

    venue: Mapped[str] = mapped_column(String(50), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(50), primary_key=True)
    interval: Mapped[str] = mapped_column(
        String(10), primary_key=True,
        comment="Candle width label: 15s, 1m, 5m, 15m, 1h, 4h, 1d",
    )
    bucket_start: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), primary_key=True,
        comment="UTC start of the candle bucket",
    )

    open: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    volume: Mapped[float] = mapped_column(
        Numeric(28, 8), nullable=False, default=0,
        comment="Traded volume (exchange_rest) or tick count (live_ticks)",
    )

    source: Mapped[str] = mapped_column(
        String(20), nullable=False, default="live_ticks",
        comment="live_ticks | exchange_rest",
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<OHLCVBar {self.venue}:{self.symbol} {self.interval} "
            f"{self.bucket_start.isoformat() if self.bucket_start else '?'} "
            f"c={self.close} src={self.source}>"
        )
