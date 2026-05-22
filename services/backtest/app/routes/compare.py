"""
Walk-forward comparison: backtest P&L vs actual live trades.

POST /backtest/compare/funding-arb
POST /backtest/compare/stat-arb

For a given strategy and lookback window the endpoint:
  1. Reads actual filled trades from the journal (via direct DB query)
  2. Runs the same backtest simulation over the same date range
  3. Returns both sets of metrics side-by-side so you can see how closely
     the backtest predicted real performance.

This is the critical feedback loop for parameter tuning — if backtest
Sharpe is 2.4 but live Sharpe is 0.3, something is wrong (slippage, latency,
funding window mismatch, look-ahead bias, etc.).

Requires: DATABASE_URL set in environment (added to compose in Phase obs/bt).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from mezna_shared.db import get_async_session
from .. import data as data_fetcher
from .. import engine
from ..config import settings as svc_settings

log = structlog.get_logger()
router = APIRouter()


# ── Request schemas ───────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    days: int = Field(30, ge=1, le=365, description="Lookback window in calendar days")
    capital_usd: float = Field(5000.0, gt=0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _actual_metrics(trades: list[dict]) -> dict:
    """Compute aggregate metrics from DB trade rows."""
    if not trades:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "total_pnl_usd": 0.0,
            "total_fees_usd": 0.0,
            "net_pnl_usd": 0.0,
            "avg_fill_price": None,
            "venues": [],
        }

    pnls = [_safe_float(t.get("realized_pnl")) for t in trades]
    fees = [_safe_float(t.get("fee")) for t in trades]
    winners = [p for p in pnls if p > 0]
    losers  = [p for p in pnls if p <= 0]
    venues  = list({t.get("venue", "unknown") for t in trades})

    fill_prices = [_safe_float(t.get("average_fill_price")) for t in trades if t.get("average_fill_price")]
    avg_fill = sum(fill_prices) / len(fill_prices) if fill_prices else None

    total_pnl = sum(pnls)
    total_fees = sum(fees)

    return {
        "total_trades": len(trades),
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "win_rate": round(len(winners) / len(trades), 4) if trades else 0.0,
        "total_pnl_usd": round(total_pnl, 4),
        "total_fees_usd": round(total_fees, 4),
        "net_pnl_usd": round(total_pnl - total_fees, 4),
        "avg_fill_price": round(avg_fill, 8) if avg_fill else None,
        "venues": venues,
    }


async def _fetch_live_trades(
    db_engine,
    strategy_type: str,
    start_dt: datetime,
    end_dt: datetime,
) -> list[dict]:
    """Read filled trades from journal for the given strategy + date range."""
    async with get_async_session(db_engine) as session:
        result = await session.execute(
            text("""
                SELECT
                    id, venue, symbol, side, strategy_type,
                    filled_qty, average_fill_price, fee,
                    realized_pnl, opened_at, filled_at, status, paper_mode
                FROM trades
                WHERE strategy_type = :strategy
                  AND status = 'filled'
                  AND opened_at >= :start_dt
                  AND opened_at <= :end_dt
                ORDER BY opened_at ASC
            """),
            {
                "strategy": strategy_type,
                "start_dt": start_dt,
                "end_dt": end_dt,
            },
        )
        rows = result.mappings().all()
        return [dict(r) for r in rows]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/compare/funding-arb")
async def compare_funding_arb(request: Request, body: CompareRequest) -> JSONResponse:
    """
    Compare funding-arb backtest simulation vs actual live trades.

    The backtest uses BTC/USDT (the dominant funding-arb pair).
    Actual trades are read directly from the journal DB for the same period.

    Returns:
      - backtest: simulation metrics over the requested period
      - actual:   aggregated metrics from real filled trades in that period
      - divergence: key deltas (P&L difference, win rate difference, etc.)
    """
    db_engine = getattr(request.app.state, "db_engine", None)
    if db_engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Database not connected — compare endpoint requires DATABASE_URL"},
        )

    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=body.days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)

    log.info("backtest.compare.funding_arb", days=body.days)

    # Fetch live trades + backtest data in parallel
    live_trades_task = asyncio.create_task(
        _fetch_live_trades(db_engine, "funding_arb", start_dt, end_dt)
    )

    async with httpx.AsyncClient() as client:
        spot_candles, funding_rates = await asyncio.gather(
            data_fetcher.fetch_candles(client, svc_settings, "BTCUSDT", "1m", start_ms, end_ms),
            data_fetcher.fetch_funding_rates(client, svc_settings, "BTCUSDT", start_ms, end_ms),
        )

    live_trades = await live_trades_task

    bt_result = engine.run_funding_arb(
        spot_candles=spot_candles,
        perp_candles=spot_candles,
        funding_rates=funding_rates,
        settings=svc_settings,
        min_edge_bps=svc_settings.DEFAULT_FUNDING_MIN_EDGE_BPS,
        fee_bps=svc_settings.DEFAULT_TAKER_FEE_BPS,
        capital_usd=body.capital_usd,
    )
    bt_result.symbol = "BTCUSDT"

    actual = _actual_metrics(live_trades)
    bt_dict = {
        "total_trades":    bt_result.total_trades,
        "win_rate":        bt_result.win_rate,
        "total_pnl_usd":  bt_result.total_pnl_usd,
        "total_fees_usd": bt_result.total_fees_usd,
        "net_pnl_usd":    bt_result.net_pnl_usd,
        "sharpe_ratio":   bt_result.sharpe_ratio,
        "max_drawdown_pct": bt_result.max_drawdown_pct,
    }

    divergence = {
        "trade_count_delta": actual["total_trades"] - bt_dict["total_trades"],
        "net_pnl_delta_usd": round(actual["net_pnl_usd"] - bt_dict["net_pnl_usd"], 4),
        "win_rate_delta":    round(actual["win_rate"] - bt_dict["win_rate"], 4),
        "note": (
            "Positive pnl_delta means live outperformed backtest. "
            "Negative means backtest was too optimistic (check slippage, timing, latency)."
        ),
    }

    return JSONResponse(content={
        "strategy":   "funding_arb",
        "period":     {"start": start_dt.isoformat(), "end": end_dt.isoformat(), "days": body.days},
        "backtest":   bt_dict,
        "actual":     actual,
        "divergence": divergence,
        "live_trade_sample": live_trades[:10],   # last 10 actual trades for reference
    })


@router.post("/compare/stat-arb")
async def compare_stat_arb(request: Request, body: CompareRequest) -> JSONResponse:
    """
    Compare stat-arb backtest simulation vs actual live trades.

    Uses BTC/USDT (dominant stat-arb pair) with default engine parameters.
    Actual trades are read from the journal for the same window.
    """
    db_engine = getattr(request.app.state, "db_engine", None)
    if db_engine is None:
        return JSONResponse(
            status_code=503,
            content={"error": "Database not connected — compare endpoint requires DATABASE_URL"},
        )

    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=body.days)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)

    log.info("backtest.compare.stat_arb", days=body.days)

    live_trades_task = asyncio.create_task(
        _fetch_live_trades(db_engine, "stat_arb", start_dt, end_dt)
    )

    async with httpx.AsyncClient() as client:
        spot_candles, perp_candles = await asyncio.gather(
            data_fetcher.fetch_candles(
                client, svc_settings, "BTCUSDT", "1h", start_ms, end_ms
            ),
            data_fetcher.fetch_perp_candles(
                client, svc_settings, "BTCUSDT", "1h", start_ms, end_ms
            ),
        )

    live_trades = await live_trades_task

    bt_result = engine.run_stat_arb(
        spot_candles=spot_candles,
        perp_candles=perp_candles,
        settings=svc_settings,
        window=svc_settings.DEFAULT_STAT_ARB_WINDOW,
        entry_z=svc_settings.DEFAULT_STAT_ARB_ENTRY_Z,
        exit_z=svc_settings.DEFAULT_STAT_ARB_EXIT_Z,
        fee_bps=svc_settings.DEFAULT_TAKER_FEE_BPS,
        capital_usd=body.capital_usd,
    )
    bt_result.symbol = "BTCUSDT/perp"
    bt_result.interval = "1h"

    actual = _actual_metrics(live_trades)
    bt_dict = {
        "total_trades":    bt_result.total_trades,
        "win_rate":        bt_result.win_rate,
        "total_pnl_usd":  bt_result.total_pnl_usd,
        "total_fees_usd": bt_result.total_fees_usd,
        "net_pnl_usd":    bt_result.net_pnl_usd,
        "sharpe_ratio":   bt_result.sharpe_ratio,
        "max_drawdown_pct": bt_result.max_drawdown_pct,
    }

    divergence = {
        "trade_count_delta": actual["total_trades"] - bt_dict["total_trades"],
        "net_pnl_delta_usd": round(actual["net_pnl_usd"] - bt_dict["net_pnl_usd"], 4),
        "win_rate_delta":    round(actual["win_rate"] - bt_dict["win_rate"], 4),
        "note": (
            "Positive pnl_delta means live outperformed backtest. "
            "Negative means backtest was too optimistic (check slippage, timing, latency)."
        ),
    }

    return JSONResponse(content={
        "strategy":   "stat_arb",
        "period":     {"start": start_dt.isoformat(), "end": end_dt.isoformat(), "days": body.days},
        "backtest":   bt_dict,
        "actual":     actual,
        "divergence": divergence,
        "live_trade_sample": live_trades[:10],
    })
