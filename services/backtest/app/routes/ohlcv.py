"""
Persisted OHLCV read endpoint.

  GET /ohlcv  — return stored candles for (venue, symbol, interval) over a recent
                window, straight from ohlcv_bars (populated by the market-data
                live bar writer + the ccxt backfill).

Lets you confirm history is accumulating and feeds DB-backed backtests. Returns
503 if the backtest service has no DB engine (DATABASE_URL unset).
"""

from datetime import datetime, timezone, timedelta

import structlog
from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from .. import data as data_fetcher

log = structlog.get_logger()
router = APIRouter()


@router.get("/ohlcv")
async def get_ohlcv(
    request: Request,
    venue: str = Query("binance", description="Venue, e.g. binance/bybit/okx/kraken"),
    symbol: str = Query("BTC/USDT", description="ccxt unified symbol, e.g. BTC/USDT"),
    interval: str = Query("1m", description="Candle interval: 15s, 1m, 5m, 1h, ..."),
    days: int = Query(7, ge=1, le=365, description="Lookback window in days"),
    limit: int = Query(5000, ge=1, le=100_000),
) -> JSONResponse:
    """Read persisted candles; newest-window first by time ascending."""
    db_engine = getattr(request.app.state, "db_engine", None)
    if db_engine is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "detail": "DATABASE_URL not configured — no persisted OHLCV"},
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    candles = await data_fetcher.fetch_candles_from_db(
        db_engine, venue, symbol, interval, start_ms, end_ms, tag_symbol=False
    )
    if limit and len(candles) > limit:
        candles = candles[-limit:]

    return JSONResponse(content={
        "status": "ok",
        "venue": venue,
        "symbol": symbol,
        "interval": interval,
        "count": len(candles),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "candles": candles,
    })
