"""
Volatility forecast endpoint.

  GET /volatility  — forecast one-step volatility (EWMA or GARCH(1,1)) for a symbol
                     from persisted OHLCV, annualised, plus the vol-target sizing
                     multiplier for a given target.

Reads ohlcv_bars (same history as /backtest and /walk-forward). 503 without a DB
engine; a clear message when there aren't enough candles.
"""

from datetime import datetime, timezone, timedelta

import structlog
from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from mezna_shared.volatility import returns_from_prices, forecast_vol, annualize, vol_target_multiplier
from .. import data as data_fetcher

log = structlog.get_logger()
router = APIRouter()

# Periods per year by candle interval (for annualisation).
_PERIODS_PER_YEAR = {
    "15s": 2_102_400, "1m": 525_600, "5m": 105_120, "15m": 35_040,
    "1h": 8_760, "4h": 2_190, "1d": 252,
}


@router.get("/volatility")
async def volatility(
    request: Request,
    venue: str = Query("binance"),
    symbol: str = Query("BTC/USDT"),
    interval: str = Query("1h"),
    days: int = Query(30, ge=1, le=365),
    method: str = Query("ewma", description="ewma | garch"),
    target_vol: float = Query(0.0, ge=0, description="Annualised target vol for the sizing multiplier (0 = skip)"),
) -> JSONResponse:
    db_engine = getattr(request.app.state, "db_engine", None)
    if db_engine is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "detail": "DATABASE_URL not configured — no persisted OHLCV"},
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    candles = await data_fetcher.fetch_candles_from_db(
        db_engine, venue, symbol, interval, int(start.timestamp() * 1000), int(end.timestamp() * 1000), tag_symbol=False,
    )
    prices = [c["close"] for c in candles if c.get("close")]
    returns = returns_from_prices(prices)
    if len(returns) < 5:
        return JSONResponse(content={
            "status": "insufficient_data", "candles": len(candles), "returns": len(returns),
            "detail": "need more OHLCV history for a vol forecast",
        })

    ppy = _PERIODS_PER_YEAR.get(interval, 252)
    vol, garch_params = forecast_vol(returns, method=method)
    vol_annual = annualize(vol, ppy)

    out = {
        "status": "ok",
        "venue": venue, "symbol": symbol, "interval": interval, "days": days,
        "method": "garch" if garch_params else "ewma",
        "returns": len(returns),
        "forecast_vol_per_period": round(vol, 8),
        "forecast_vol_annualized": round(vol_annual, 6),
        "periods_per_year": ppy,
        "garch_params": garch_params,
    }
    if target_vol > 0:
        out["target_vol_annualized"] = target_vol
        out["sizing_multiplier"] = round(vol_target_multiplier(vol_annual, target_vol), 4)
    return JSONResponse(content=out)
