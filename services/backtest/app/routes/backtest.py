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
from fastapi import APIRouter, Request, status
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
    # Data source: "binance" = live REST (default behaviour); "db" = persisted
    # ohlcv_bars only; "auto" = DB when available + populated, else REST. For the
    # DB path, spot_symbol/perp_symbol are ccxt unified symbols (e.g. BTC/USDT,
    # BTC/USDT:USDT) under `venue`.
    source: str = Field("auto", pattern="^(auto|binance|db)$")
    venue: str = Field("binance", description="Venue for the DB source")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_range_ms(days: int) -> tuple[int, int]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _result_to_dict(result: engine.BacktestResult) -> dict:
    d = vars(result).copy()
    # trade_log and equity_curve are already lists of dicts (via vars(TradeRecord))
    return d


async def _load_stat_arb_candles(
    body: "StatArbRequest", request: Request, start_ms: int, end_ms: int
) -> tuple[list[dict] | None, list[dict] | None, str]:
    """
    Load spot + perp candles for a stat-arb run, honouring body.source.

    Returns (spot, perp, data_source). When source resolves to the DB but no
    persisted bars exist, returns (None, None, "db") so an explicit db request can
    surface a clear error; "auto"/"binance" then fall through to the REST fetch.
    """
    db_engine = getattr(request.app.state, "db_engine", None)
    want_db = body.source == "db" or (body.source == "auto" and db_engine is not None)

    if want_db and db_engine is not None:
        spot = await data_fetcher.fetch_candles_from_db(
            db_engine, body.venue, body.spot_symbol, body.interval, start_ms, end_ms
        )
        perp = await data_fetcher.fetch_candles_from_db(
            db_engine, body.venue, body.perp_symbol, body.interval, start_ms, end_ms
        )
        if spot and perp:
            return spot, perp, "db"
        if body.source == "db":
            return None, None, "db"   # explicit DB request, no data → caller 404s

    async with httpx.AsyncClient() as client:
        spot, perp = await _fetch_with_error(
            data_fetcher.fetch_candles(
                client, svc_settings, body.spot_symbol, body.interval, start_ms, end_ms
            ),
            data_fetcher.fetch_perp_candles(
                client, svc_settings, body.perp_symbol, body.interval, start_ms, end_ms
            ),
        )
    return spot, perp, "binance"


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
async def run_stat_arb(body: StatArbRequest, request: Request) -> JSONResponse:
    """
    Run a statistical arbitrage backtest on spot vs perp spread.

    Loads spot and perp candles (from persisted ohlcv_bars or Binance REST — see
    `source`), computes the rolling z-score of the price spread, and simulates
    entry/exit based on z-score thresholds.

    Strategy:
      z > entry_z → sell spot, buy perp (spot is overpriced)
      z < -entry_z → buy spot, sell perp (perp is overpriced)
      |z| < exit_z → close both legs (spread reverted to mean)

    Returns: trade log, equity curve, Sharpe ratio, max drawdown, win rate,
    and `data_source` (db | binance).
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
        source=body.source,
    )

    spot_candles, perp_candles, data_source = await _load_stat_arb_candles(
        body, request, start_ms, end_ms
    )
    if spot_candles is None or perp_candles is None:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "status": "error",
                "detail": (
                    f"no persisted OHLCV for {body.venue}:{body.spot_symbol}/"
                    f"{body.perp_symbol} interval={body.interval}. Backfill first "
                    f"(POST market-data /backfill) or use source=binance."
                ),
            },
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

    d = _result_to_dict(result)
    d["data_source"] = data_source
    return JSONResponse(content=d)


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


# ── Monte Carlo endpoints ─────────────────────────────────────────────────────

class MonteCarloRequest(BaseModel):
    symbol: str = Field("BTCUSDT")
    days: int = Field(90, ge=7, le=365)
    capital_usd: float = Field(5000.0, gt=0)
    min_edge_bps: float = Field(5.0, gt=0)
    fee_bps: float = Field(7.5, gt=0)
    n_simulations: int = Field(1000, ge=100, le=10000)
    kelly_multiplier: float = Field(0.5, ge=0.1, le=1.0)


@router.post("/monte-carlo/funding-arb")
async def monte_carlo_funding_arb(body: MonteCarloRequest) -> JSONResponse:
    """
    Run funding-arb backtest then Monte Carlo stress test.

    Bootstrap-resamples the trade log across N simulated paths and returns
    the distribution of final equity and max drawdown.  Use this to understand
    the worst-case scenario, not just the point estimate.

    Key outputs:
      equity_p10 / p50 / p90   — 10th/median/90th percentile ending capital
      max_dd_p90               — 90th-pct max drawdown (worst 10% of paths)
      ruin_prob_50pct          — probability of losing ≥50% of capital
      kelly_fraction           — optimal half-Kelly position sizing
    """
    start_ms, end_ms = _date_range_ms(body.days)

    async with httpx.AsyncClient() as client:
        spot_candles, funding_rates = await _fetch_with_error(
            data_fetcher.fetch_candles(
                client, svc_settings, body.symbol, "1m", start_ms, end_ms
            ),
            data_fetcher.fetch_funding_rates(
                client, svc_settings, body.symbol, start_ms, end_ms
            ),
        )

    for c in spot_candles:
        c["symbol"] = body.symbol

    bt = engine.run_funding_arb(
        spot_candles=spot_candles,
        perp_candles=spot_candles,
        funding_rates=funding_rates,
        settings=svc_settings,
        min_edge_bps=body.min_edge_bps,
        fee_bps=body.fee_bps,
        capital_usd=body.capital_usd,
    )
    bt.symbol = body.symbol

    mc = engine.run_monte_carlo(
        backtest_result=bt,
        n_simulations=body.n_simulations,
        kelly_multiplier=body.kelly_multiplier,
    )

    return JSONResponse(content={
        "backtest_summary": {
            "total_trades":      bt.total_trades,
            "net_pnl_usd":       bt.net_pnl_usd,
            "sharpe_ratio":      bt.sharpe_ratio,
            "sortino_ratio":     bt.sortino_ratio,
            "calmar_ratio":      bt.calmar_ratio,
            "max_drawdown_pct":  bt.max_drawdown_pct,
            "win_rate":          bt.win_rate,
            "total_return_pct":  bt.total_return_pct,
        },
        "monte_carlo": vars(mc),
        "interpretation": {
            "equity_p50": f"50% of simulated paths end above ${mc.equity_p50:,.2f}",
            "equity_p10": f"10% of paths end below ${mc.equity_p10:,.2f} (downside scenario)",
            "max_dd_p90": f"In 10% of paths drawdown exceeds {mc.max_dd_p90:.1f}%",
            "ruin_25pct": f"Probability of losing >25% of capital: {mc.ruin_prob_25pct*100:.1f}%",
            "kelly":      f"Half-Kelly optimal position size: {mc.kelly_position_pct:.2f}% of capital",
        },
    })


@router.post("/monte-carlo/stat-arb")
async def monte_carlo_stat_arb(body: StatArbRequest) -> JSONResponse:
    """
    Run stat-arb backtest then Monte Carlo stress test.

    Same methodology as /monte-carlo/funding-arb but for the z-score strategy.
    Uses default n_simulations=1000 and half-Kelly multiplier.
    """
    start_ms, end_ms = _date_range_ms(body.days)

    async with httpx.AsyncClient() as client:
        spot_candles, perp_candles = await _fetch_with_error(
            data_fetcher.fetch_candles(
                client, svc_settings, body.spot_symbol, body.interval, start_ms, end_ms
            ),
            data_fetcher.fetch_perp_candles(
                client, svc_settings, body.perp_symbol, body.interval, start_ms, end_ms
            ),
        )

    bt = engine.run_stat_arb(
        spot_candles=spot_candles,
        perp_candles=perp_candles,
        settings=svc_settings,
        window=body.window,
        entry_z=body.entry_z,
        exit_z=body.exit_z,
        fee_bps=body.fee_bps,
        capital_usd=body.capital_usd,
    )
    bt.symbol = f"{body.spot_symbol}/{body.perp_symbol}"
    bt.interval = body.interval

    mc = engine.run_monte_carlo(backtest_result=bt)

    return JSONResponse(content={
        "backtest_summary": {
            "total_trades":     bt.total_trades,
            "net_pnl_usd":      bt.net_pnl_usd,
            "sharpe_ratio":     bt.sharpe_ratio,
            "sortino_ratio":    bt.sortino_ratio,
            "calmar_ratio":     bt.calmar_ratio,
            "max_drawdown_pct": bt.max_drawdown_pct,
            "win_rate":         bt.win_rate,
            "total_return_pct": bt.total_return_pct,
        },
        "monte_carlo": vars(mc),
        "interpretation": {
            "equity_p50": f"50% of simulated paths end above ${mc.equity_p50:,.2f}",
            "equity_p10": f"10% of paths end below ${mc.equity_p10:,.2f}",
            "max_dd_p90": f"Worst 10% of paths see drawdown >{mc.max_dd_p90:.1f}%",
            "kelly":      f"Half-Kelly sizing: {mc.kelly_position_pct:.2f}% of capital per trade",
        },
    })


# ── Grid search / optimisation endpoints ─────────────────────────────────────

class FundingArbOptRequest(BaseModel):
    symbol: str = Field("BTCUSDT")
    days: int = Field(90, ge=30, le=365)
    capital_usd: float = Field(5000.0, gt=0)
    min_edge_grid: list[float] = Field(
        default=[3.0, 5.0, 7.0, 10.0, 15.0],
        description="min_edge_bps values to test",
    )
    fee_grid: list[float] = Field(
        default=[7.5, 10.0, 15.0],
        description="fee_bps values to test",
    )


class StatArbOptRequest(BaseModel):
    spot_symbol: str = Field("BTCUSDT")
    perp_symbol: str = Field("BTCUSDT")
    interval: str = Field("1h")
    days: int = Field(120, ge=30, le=365)
    capital_usd: float = Field(5000.0, gt=0)
    window_grid: list[int] = Field(
        default=[50, 75, 100, 150, 200],
        description="z-score window sizes to test",
    )
    entry_z_grid: list[float] = Field(
        default=[1.5, 2.0, 2.5, 3.0],
        description="entry z-score thresholds to test",
    )
    exit_z: float = Field(0.5, ge=0.0)
    fee_bps: float = Field(7.5, gt=0)


@router.post("/optimize/funding-arb")
async def optimize_funding_arb(body: FundingArbOptRequest) -> JSONResponse:
    """
    Grid search over funding arb parameters.

    Downloads historical data ONCE then runs all parameter combinations
    against it — no repeated API calls.  Returns all results sorted by
    Sharpe ratio so you can find the optimal parameter set.

    Useful before deploying strategy changes to see which min_edge threshold
    and fee assumption produces the best risk-adjusted return historically.
    """
    start_ms, end_ms = _date_range_ms(body.days)

    async with httpx.AsyncClient() as client:
        spot_candles, funding_rates = await _fetch_with_error(
            data_fetcher.fetch_candles(
                client, svc_settings, body.symbol, "1m", start_ms, end_ms
            ),
            data_fetcher.fetch_funding_rates(
                client, svc_settings, body.symbol, start_ms, end_ms
            ),
        )

    for c in spot_candles:
        c["symbol"] = body.symbol

    results = engine.grid_search_funding_arb(
        spot_candles=spot_candles,
        perp_candles=spot_candles,
        funding_rates=funding_rates,
        settings=svc_settings,
        capital_usd=body.capital_usd,
        min_edge_grid=body.min_edge_grid,
        fee_grid=body.fee_grid,
    )

    best = results[0] if results else None
    return JSONResponse(content={
        "strategy":          "funding_arb",
        "symbol":            body.symbol,
        "period_days":       body.days,
        "combinations_run":  len(results),
        "best_params":       best["params"] if best else None,
        "best_sharpe":       best["sharpe_ratio"] if best else None,
        "ranked_results":    results,
    })


@router.post("/optimize/stat-arb")
async def optimize_stat_arb(body: StatArbOptRequest) -> JSONResponse:
    """
    Grid search over stat arb parameters (window size × entry z-score).

    Downloads data once and runs all window/z-score combinations.
    Returns the parameter set with the best Sharpe ratio — use this
    to update STAT_ARB_WINDOW and STAT_ARB_ENTRY_Z in your .env.

    Note: out-of-sample validation is essential — do not just take the
    in-sample best.  Run a separate period to confirm the parameters hold.
    """
    start_ms, end_ms = _date_range_ms(body.days)

    async with httpx.AsyncClient() as client:
        spot_candles, perp_candles = await _fetch_with_error(
            data_fetcher.fetch_candles(
                client, svc_settings, body.spot_symbol, body.interval, start_ms, end_ms
            ),
            data_fetcher.fetch_perp_candles(
                client, svc_settings, body.perp_symbol, body.interval, start_ms, end_ms
            ),
        )

    results = engine.grid_search_stat_arb(
        spot_candles=spot_candles,
        perp_candles=perp_candles,
        settings=svc_settings,
        capital_usd=body.capital_usd,
        window_grid=body.window_grid,
        entry_z_grid=body.entry_z_grid,
        exit_z=body.exit_z,
        fee_bps=body.fee_bps,
    )

    best = results[0] if results else None
    return JSONResponse(content={
        "strategy":          "stat_arb",
        "symbol":            f"{body.spot_symbol}/{body.perp_symbol}",
        "period_days":       body.days,
        "combinations_run":  len(results),
        "best_params":       best["params"] if best else None,
        "best_sharpe":       best["sharpe_ratio"] if best else None,
        "ranked_results":    results,
        "warning": (
            "In-sample optimisation only. Always validate on a held-out period "
            "before deploying optimised parameters to live trading."
        ),
    })
