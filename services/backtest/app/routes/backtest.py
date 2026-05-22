"""
Backtest API endpoints.

POST /backtest/funding-arb  — run funding rate arb simulation
POST /backtest/stat-arb     — run stat arb simulation
GET  /backtest/symbols      — list available symbols (from Binance)

All endpoints download live historical data from Binance's public API.
No auth needed.  Requests may take 5–30 seconds depending on date range.
"""

from datetime import datetime, timezone, timedelta

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import data as data_fetcher
from .. import engine
from ..config import settings as svc_settings

log = structlog.get_logger()
router = APIRouter()


# ── Request schemas ───────────────────────────────────────────────────────────

class FundingArbRequest(BaseModel):
    symbol: str = Field("BTCUSDT", description="Binance symbol, e.g. BTCUSDT")
    days: int = Field(30, ge=1, le=365, description="Historical lookback in calendar days")
    capital_usd: float = Field(5000.0, gt=0)
    min_edge_bps: float = Field(5.0, gt=0, description="Minimum funding edge above fees")
    fee_bps: float = Field(7.5, gt=0, description="Taker fee in basis points")


class StatArbRequest(BaseModel):
    spot_symbol: str = Field("BTCUSDT", description="Spot symbol (Binance), e.g. BTCUSDT")
    perp_symbol: str = Field("BTCUSDT", description="Perp symbol (FAPI), e.g. BTCUSDT")
    interval: str = Field("1h", description="Candle interval: 1m, 5m, 15m, 1h, 4h, 1d")
    days: int = Field(90, ge=7, le=365)
    window: int = Field(100, ge=20, le=500, description="Rolling z-score window (candles)")
    entry_z: float = Field(2.0, gt=0.5, description="Z-score entry threshold")
    exit_z: float = Field(0.5, ge=0.0, description="Z-score exit threshold")
    capital_usd: float = Field(5000.0, gt=0)
    fee_bps: float = Field(7.5, gt=0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_range_ms(days: int) -> tuple[int, int]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _result_to_dict(result: engine.BacktestResult) -> dict:
    d = vars(result).copy()
    # trade_log and equity_curve are already lists of dicts (via vars(TradeRecord))
    return d


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/funding-arb")
async def run_funding_arb(body: FundingArbRequest) -> JSONResponse:
    """
    Run a funding rate arbitrage backtest.

    Downloads 1m spot candles + funding rate history from Binance's public API,
    then simulates the strategy over the requested date range.

    Strategy: enter long spot + short perp when funding_rate_bps > min_edge + fees.
    Exit after next funding period (8h). Collect funding payment as income.

    Returns: trade log, equity curve, Sharpe ratio, max drawdown, win rate.
    """
    start_ms, end_ms = _date_range_ms(body.days)

    log.info(
        "backtest.funding_arb_request",
        symbol=body.symbol,
        days=body.days,
        min_edge_bps=body.min_edge_bps,
    )

    async with httpx.AsyncClient() as client:
        spot_candles, funding_rates = await _fetch_with_error(
            data_fetcher.fetch_candles(
                client, svc_settings, body.symbol, "1m", start_ms, end_ms
            ),
            data_fetcher.fetch_funding_rates(
                client, svc_settings, body.symbol, start_ms, end_ms
            ),
        )

    # Mark symbol on candles for display
    for c in spot_candles:
        c["symbol"] = body.symbol

    result = engine.run_funding_arb(
        spot_candles=spot_candles,
        perp_candles=spot_candles,  # use spot price as proxy for perp price in 1m sim
        funding_rates=funding_rates,
        settings=svc_settings,
        min_edge_bps=body.min_edge_bps,
        fee_bps=body.fee_bps,
        capital_usd=body.capital_usd,
    )
    result.symbol = body.symbol

    return JSONResponse(content=_result_to_dict(result))


@router.post("/stat-arb")
async def run_stat_arb(body: StatArbRequest) -> JSONResponse:
    """
    Run a statistical arbitrage backtest on spot vs perp spread.

    Downloads both spot and perp candles, computes rolling z-score of the
    price spread, and simulates entry/exit based on z-score thresholds.

    Strategy:
      z > entry_z → sell spot, buy perp (spot is overpriced)
      z < -entry_z → buy spot, sell perp (perp is overpriced)
      |z| < exit_z → close both legs (spread reverted to mean)

    Returns: trade log, equity curve, Sharpe ratio, max drawdown, win rate.
    """
    start_ms, end_ms = _date_range_ms(body.days)

    log.info(
        "backtest.stat_arb_request",
        spot=body.spot_symbol,
        perp=body.perp_symbol,
        interval=body.interval,
        days=body.days,
        window=body.window,
        entry_z=body.entry_z,
    )

    async with httpx.AsyncClient() as client:
        spot_candles, perp_candles = await _fetch_with_error(
            data_fetcher.fetch_candles(
                client, svc_settings, body.spot_symbol, body.interval, start_ms, end_ms
            ),
            data_fetcher.fetch_perp_candles(
                client, svc_settings, body.perp_symbol, body.interval, start_ms, end_ms
            ),
        )

    result = engine.run_stat_arb(
        spot_candles=spot_candles,
        perp_candles=perp_candles,
        settings=svc_settings,
        window=body.window,
        entry_z=body.entry_z,
        exit_z=body.exit_z,
        fee_bps=body.fee_bps,
        capital_usd=body.capital_usd,
    )
    result.symbol = f"{body.spot_symbol}/{body.perp_symbol}"
    result.interval = body.interval

    return JSONResponse(content=_result_to_dict(result))


@router.get("/symbols")
async def list_symbols() -> JSONResponse:
    """
    Return a curated list of symbol pairs suitable for backtesting.

    These are the highest-liquidity spot/perp pairs on Binance — the same
    pairs used by the live funding_arb and stat_arb strategies.
    """
    return JSONResponse(content={
        "funding_arb_symbols": [
            {"spot": "BTCUSDT", "perp": "BTCUSDT", "label": "BTC/USDT"},
            {"spot": "ETHUSDT", "perp": "ETHUSDT", "label": "ETH/USDT"},
            {"spot": "SOLUSDT", "perp": "SOLUSDT", "label": "SOL/USDT"},
            {"spot": "BNBUSDT", "perp": "BNBUSDT", "label": "BNB/USDT"},
        ],
        "stat_arb_pairs": [
            {"spot": "BTCUSDT", "perp": "BTCUSDT", "label": "BTC spot vs perp"},
            {"spot": "ETHUSDT", "perp": "ETHUSDT", "label": "ETH spot vs perp"},
        ],
        "intervals": ["1m", "5m", "15m", "1h", "4h", "1d"],
    })


async def _fetch_with_error(*awaitables):
    """Await multiple coroutines, re-raising with a user-friendly error."""
    import asyncio
    try:
        return await asyncio.gather(*awaitables)
    except Exception as exc:
        log.error("backtest.data_fetch_failed", error=str(exc))
        raise
