"""
Walk-forward analysis endpoint.

  POST /walk-forward/stat-arb  — rolling out-of-sample validation of stat-arb over
                                 persisted OHLCV. Optimise (window, entry_z) on each
                                 in-sample window, test on the next out-of-sample
                                 window, and report whether the edge holds (the
                                 walk-forward efficiency + verdict).

Reads from ohlcv_bars (same persisted history as /backtest and /ohlcv). 503 when
the service has no DB engine; a clear insufficient-data response when history is
too short for even one IS+OOS split.
"""

from datetime import datetime, timezone, timedelta

import structlog
from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import data as data_fetcher
from .. import walk_forward as wf
from ..config import settings as svc_settings

log = structlog.get_logger()
router = APIRouter()


class WalkForwardStatArbRequest(BaseModel):
    venue: str = Field("binance")
    spot_symbol: str = Field("BTC/USDT")
    perp_symbol: str = Field("BTC/USDT:USDT")
    interval: str = Field("1h")
    days: int = Field(180, ge=30, le=365)
    capital_usd: float = Field(5000.0, gt=0)
    window_grid: list[int] = Field(default=[50, 75, 100, 150])
    entry_z_grid: list[float] = Field(default=[1.5, 2.0, 2.5, 3.0])
    exit_z: float = Field(0.5, ge=0.0)
    fee_bps: float = Field(7.5, gt=0)
    is_candles: int = Field(500, ge=50, description="In-sample window size (candles)")
    oos_candles: int = Field(150, ge=20, description="Out-of-sample window size (candles)")
    step_candles: int = Field(150, ge=10, description="Roll step (default = OOS = non-overlapping)")


@router.post("/walk-forward/stat-arb")
async def walk_forward_stat_arb(body: WalkForwardStatArbRequest, request: Request) -> JSONResponse:
    db_engine = getattr(request.app.state, "db_engine", None)
    if db_engine is None:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "detail": "DATABASE_URL not configured — no persisted OHLCV"},
        )

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=body.days)
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    spot = await data_fetcher.fetch_candles_from_db(
        db_engine, body.venue, body.spot_symbol, body.interval, start_ms, end_ms, tag_symbol=False
    )
    perp = await data_fetcher.fetch_candles_from_db(
        db_engine, body.venue, body.perp_symbol, body.interval, start_ms, end_ms, tag_symbol=False
    )

    needed = body.is_candles + body.oos_candles
    if len(spot) < needed or len(perp) < needed:
        return JSONResponse(content={
            "status": "insufficient_data",
            "detail": f"need ≥{needed} candles per leg for one IS+OOS split",
            "spot_candles": len(spot),
            "perp_candles": len(perp),
            "hint": "accumulate more OHLCV history or lower is_candles/oos_candles",
        })

    result = wf.walk_forward_stat_arb(
        spot, perp, svc_settings,
        window_grid=body.window_grid,
        entry_z_grid=body.entry_z_grid,
        exit_z=body.exit_z,
        fee_bps=body.fee_bps,
        capital_usd=body.capital_usd,
        is_size=body.is_candles,
        oos_size=body.oos_candles,
        step=body.step_candles,
    )

    return JSONResponse(content={
        "status": "ok",
        "strategy": "stat_arb",
        "venue": body.venue,
        "symbols": f"{body.spot_symbol} / {body.perp_symbol}",
        "interval": body.interval,
        "days": body.days,
        "is_candles": body.is_candles,
        "oos_candles": body.oos_candles,
        "step_candles": body.step_candles,
        **result,
    })
