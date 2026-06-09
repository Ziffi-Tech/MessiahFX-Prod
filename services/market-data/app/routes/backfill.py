"""
OHLCV backfill endpoint.

  POST /backfill  — pull historical candles from an exchange's public OHLCV
                    endpoint into ohlcv_bars (source=exchange_rest).

On-demand and operator-triggered (no auth at the service; the gateway fronts it).
Crypto venues only (binance/bybit/okx/kraken) — see app/backfill.py.
"""

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import backfill as backfill_mod
from ..backfill import CCXT_VENUES

log = structlog.get_logger()
router = APIRouter()


class BackfillRequest(BaseModel):
    venue: str = Field("binance", description=f"One of: {sorted(CCXT_VENUES)}")
    symbol: str = Field("BTC/USDT", description="ccxt unified symbol, e.g. BTC/USDT or BTC/USDT:USDT")
    timeframe: str = Field("1m", description="Candle interval: 1m, 5m, 15m, 1h, 4h, 1d")
    days: int = Field(7, ge=1, le=365, description="Calendar days of history to pull")
    incremental: bool = Field(True, description="Resume from the newest stored bucket")


@router.post("/backfill")
async def run_backfill(body: BackfillRequest, request: Request) -> JSONResponse:
    """
    Backfill historical OHLCV for one (venue, symbol). Returns a write summary.

    503 if the DB engine is unavailable; 400 for an unsupported venue or bad
    timeframe; 502 if the exchange fetch fails.
    """
    db_engine = getattr(request.app.state, "db_engine", None)
    if db_engine is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "detail": "database unavailable"},
        )

    if body.venue not in CCXT_VENUES:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "error", "detail": f"unsupported venue {body.venue!r}; ccxt venues: {sorted(CCXT_VENUES)}"},
        )

    try:
        summary = await backfill_mod.backfill_symbol(
            db_engine,
            body.venue,
            body.symbol,
            timeframe=body.timeframe,
            days=body.days,
            incremental=body.incremental,
        )
    except ValueError as exc:  # bad timeframe label / unsupported venue
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "error", "detail": str(exc)},
        )
    except Exception as exc:
        log.error("backfill.failed", venue=body.venue, symbol=body.symbol, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"status": "error", "detail": f"exchange fetch failed: {exc}"},
        )

    return JSONResponse(content={"status": "ok", **summary})
