"""
Regime-Conditional Backtesting — parameter sweeps and volatility-regime splits.

POST /backtest/funding-arb/sweep  — funding_arb across multiple min_edge_bps values
POST /backtest/stat-arb/sweep     — stat_arb across multiple entry_z thresholds
POST /backtest/regime-split       — backtest split by realised volatility regime

Sweep endpoints answer: "what threshold maximises risk-adjusted return?"
Each sweep downloads data ONCE and runs multiple simulations sequentially —
faster than N separate backtest calls, and the results are directly comparable.

Regime-split answers: "does this strategy's edge depend on market volatility?"
Classifies each trade by the realised volatility at entry time into terciles:
  low_vol  — calm/ranging conditions
  mid_vol  — normal conditions
  high_vol — elevated volatility (volatile, crisis)

Knowing which regime drives your P&L lets you size down in hostile conditions.
"""

import asyncio
import math
import statistics
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import data as data_fetcher
from .. import engine
from ..config import settings as svc_settings

log = structlog.get_logger()
router = APIRouter()


# ── Request schemas ───────────────────────────────────────────────────────────

class FundingArbSweepRequest(BaseModel):
    symbol: str = Field("BTCUSDT", description="Binance symbol, e.g. BTCUSDT")
    days: int = Field(30, ge=7, le=90, description="Historical lookback in days")
    capital_usd: float = Field(5000.0, gt=0)
    fee_bps: float = Field(7.5, gt=0)
    min_edge_bps_values: list[float] = Field(
        default=[3.0, 4.0, 5.0, 6.0, 7.5, 10.0],
        min_length=2,
        max_length=12,
        description="List of min_edge_bps values to sweep (2–12 values).",
    )


class StatArbSweepRequest(BaseModel):
    spot_symbol: str = Field("BTCUSDT")
    perp_symbol: str = Field("BTCUSDT")
    interval: str = Field("1h", description="Candle interval: 1m, 5m, 15m, 1h, 4h")
    days: int = Field(60, ge=14, le=90)
    window: int = Field(100, ge=20, le=500, description="Z-score rolling window (candles)")
    exit_z: float = Field(0.5, ge=0.0, description="Z-score exit threshold")
    capital_usd: float = Field(5000.0, gt=0)
    fee_bps: float = Field(7.5, gt=0)
    entry_z_values: list[float] = Field(
        default=[1.5, 1.75, 2.0, 2.25, 2.5, 3.0],
        min_length=2,
        max_length=12,
        description="List of entry Z-score thresholds to sweep (2–12 values).",
    )


class RegimeSplitRequest(BaseModel):
    """
    Run a backtest and split the trade log by realised-volatility regime.

    Volatility is computed from the spot candles using a rolling window.
    Trades are then classified into terciles (low_vol / mid_vol / high_vol)
    based on the realised vol at each trade's entry time.
    """
    strategy: str = Field(description="funding_arb or stat_arb")
    symbol: str = Field("BTCUSDT", description="Primary symbol (Binance spot)")
    days: int = Field(60, ge=14, le=90)
    capital_usd: float = Field(5000.0, gt=0)
    fee_bps: float = Field(7.5, gt=0)
    # funding_arb params
    min_edge_bps: float = Field(5.0, gt=0)
    # stat_arb params
    perp_symbol: str = Field("BTCUSDT")
    interval: str = Field("1h")
    window: int = Field(100, ge=20, le=500)
    entry_z: float = Field(2.0, gt=0.5)
    exit_z: float = Field(0.5, ge=0.0)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_range_ms(days: int) -> tuple[int, int]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _sweep_metrics(result: engine.BacktestResult, param_value: Any) -> dict:
    """Extract key metrics for one sweep run."""
    return {
        "param_value": param_value,
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "total_return_pct": result.total_return_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "max_drawdown_pct": result.max_drawdown_pct,
        "avg_hold_candles": result.avg_hold_candles,
        "net_pnl_usd": result.net_pnl_usd,
        "total_fees_usd": result.total_fees_usd,
    }


def _rolling_realised_vol(candles: list[dict], window: int) -> list[float]:
    """
    Compute rolling realised volatility (std dev of log returns × sqrt(window)).
    Returns one float per candle; first `window` entries are NaN.
    """
    closes = [float(c.get("close", 0) or 0) for c in candles]
    log_rets: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            log_rets.append(math.log(closes[i] / closes[i - 1]))
        else:
            log_rets.append(0.0)

    vols: list[float] = [float("nan")] * len(candles)
    for i in range(window, len(candles)):
        chunk = log_rets[i - window: i]
        if len(chunk) >= 2:
            vols[i] = statistics.stdev(chunk) * math.sqrt(window)
    return vols


def _to_epoch_ms(ts: object) -> int | None:
    """
    Normalise any timestamp representation to integer epoch-milliseconds.

    Handles:
      - int / float  already in epoch-ms (>= 1e10) or epoch-seconds (< 1e10)
      - str          ISO-8601 with or without timezone / 'Z' suffix
      - None / empty → None
    """
    if ts is None or ts == "":
        return None
    try:
        if isinstance(ts, (int, float)):
            val = int(ts)
            # Distinguish epoch-ms (13 digits) from epoch-s (10 digits)
            return val if val > 9_999_999_999 else val * 1000
        if isinstance(ts, str):
            # Remove trailing Z and normalise offset
            normalised = ts.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalised)
            return int(dt.timestamp() * 1000)
    except Exception:
        pass
    return None


def _classify_trades_by_vol(
    trade_log: list[dict],
    candles: list[dict],
    vols: list[float],
) -> dict[str, list[dict]]:
    """
    Assign each trade to a volatility tercile (low / mid / high) based on the
    realised vol of the candle immediately before the trade's entry time.

    Timestamp normalisation:
      - candle open_time: int epoch-ms OR int epoch-s OR ISO string → normalised via _to_epoch_ms
      - trade entry_ts:   ISO string OR int epoch-ms OR int epoch-s → normalised via _to_epoch_ms
      Both paths go through the same _to_epoch_ms helper so format mismatches
      cannot produce silent wrong assignments.

    Fallback: if fewer than 2 trades can be matched to a valid vol, all trades
    are placed in mid_vol (labelled "unclassified") rather than silently dropped.
    """
    if not trade_log:
        return {"low_vol": [], "mid_vol": [], "high_vol": []}

    # Normalise all candle timestamps to epoch-ms integers
    candle_times_ms: list[int | None] = [
        _to_epoch_ms(c.get("open_time")) for c in candles
    ]

    def _vol_at_entry(raw_entry_ts: object) -> float | None:
        """Return the vol for the most recent candle at or before entry_ts."""
        entry_ms = _to_epoch_ms(raw_entry_ts)
        if entry_ms is None:
            return None

        # Binary-search for the rightmost candle_time ≤ entry_ms
        lo, hi, best = 0, len(candle_times_ms) - 1, -1
        while lo <= hi:
            mid_idx = (lo + hi) // 2
            ct = candle_times_ms[mid_idx]
            if ct is None:
                lo = mid_idx + 1
                continue
            if ct <= entry_ms:
                best = mid_idx
                lo = mid_idx + 1
            else:
                hi = mid_idx - 1

        if best < 0:
            return None
        v = vols[best]
        return None if math.isnan(v) else v

    # Pair each trade with its vol
    paired = [
        (t, _vol_at_entry(t.get("entry_ts") or t.get("open_time")))
        for t in trade_log
    ]
    valid = [(t, v) for t, v in paired if v is not None]
    unmatched = [t for t, v in paired if v is None]

    if len(valid) < 2:
        # Cannot form meaningful terciles — put everything in mid_vol
        log.warning(
            "regime_split.vol_match_failed",
            matched=len(valid),
            total=len(trade_log),
            hint="entry_ts format may not match candle open_time — check both are epoch-ms or ISO strings",
        )
        return {"low_vol": [], "mid_vol": trade_log, "high_vol": []}

    sorted_vols = sorted(v for _, v in valid)
    n = len(sorted_vols)
    q33 = sorted_vols[n // 3]
    q67 = sorted_vols[(2 * n) // 3]

    low, mid, high = [], [], []
    for trade, vol in valid:
        if vol <= q33:
            low.append(trade)
        elif vol <= q67:
            mid.append(trade)
        else:
            high.append(trade)

    # Unmatched trades go to mid_vol with a flag so they're not silently lost
    for trade in unmatched:
        mid.append({**trade, "_vol_unmatched": True})

    return {"low_vol": low, "mid_vol": mid, "high_vol": high}


def _aggregate(trades: list[dict], capital_usd: float) -> dict:
    """Summary metrics from a list of trade dicts."""
    if not trades:
        return {
            "count": 0,
            "win_rate": 0.0,
            "net_pnl_usd": 0.0,
            "total_return_pct": 0.0,
            "avg_pnl_usd": 0.0,
        }
    net_pnls = [t.get("net_pnl_usd", 0.0) for t in trades]
    wins = [p for p in net_pnls if p > 0]
    total_net = sum(net_pnls)
    return {
        "count": len(trades),
        "win_rate": round(len(wins) / len(trades), 4),
        "net_pnl_usd": round(total_net, 4),
        "total_return_pct": round(total_net / capital_usd * 100, 4) if capital_usd else 0.0,
        "avg_pnl_usd": round(total_net / len(trades), 4),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/funding-arb/sweep")
async def funding_arb_sweep(body: FundingArbSweepRequest) -> JSONResponse:
    """
    Parameter sweep: run funding_arb for each min_edge_bps value.

    Downloads Binance data ONCE then runs each simulation in sequence —
    efficient and directly comparable. Returns a sensitivity table plus
    the optimal threshold by Sharpe ratio and by total return.

    Typical latency: 10–25 seconds (one data download, N simulations).
    """
    start_ms, end_ms = _date_range_ms(body.days)

    log.info(
        "backtest.funding_arb_sweep",
        symbol=body.symbol,
        days=body.days,
        n_params=len(body.min_edge_bps_values),
    )

    async with httpx.AsyncClient() as client:
        try:
            spot_candles, funding_rates = await asyncio.gather(
                data_fetcher.fetch_candles(
                    client, svc_settings, body.symbol, "1m", start_ms, end_ms
                ),
                data_fetcher.fetch_funding_rates(
                    client, svc_settings, body.symbol, start_ms, end_ms
                ),
            )
        except Exception as exc:
            log.error("backtest.sweep_data_failed", error=str(exc))
            return JSONResponse(
                status_code=502,
                content={"error": f"Data fetch failed: {str(exc)[:120]}"},
            )

    for c in spot_candles:
        c["symbol"] = body.symbol

    results = []
    for edge in sorted(set(body.min_edge_bps_values)):
        r = engine.run_funding_arb(
            spot_candles=spot_candles,
            perp_candles=spot_candles,
            funding_rates=funding_rates,
            settings=svc_settings,
            min_edge_bps=edge,
            fee_bps=body.fee_bps,
            capital_usd=body.capital_usd,
        )
        r.symbol = body.symbol
        results.append(_sweep_metrics(r, edge))

    return JSONResponse(content={
        "strategy": "funding_arb",
        "symbol": body.symbol,
        "days": body.days,
        "sweep_param": "min_edge_bps",
        "results": results,
        "optimal_by_sharpe": (
            max(results, key=lambda x: x["sharpe_ratio"])["param_value"]
            if results else None
        ),
        "optimal_by_return": (
            max(results, key=lambda x: x["total_return_pct"])["param_value"]
            if results else None
        ),
        "note": "Optimal values are in-sample. Validate on out-of-sample data before deploying.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@router.post("/stat-arb/sweep")
async def stat_arb_sweep(body: StatArbSweepRequest) -> JSONResponse:
    """
    Parameter sweep: run stat_arb for each entry_z threshold.

    Higher Z = fewer but higher-quality signals.
    Lower Z = more signals but more noise. The sweep reveals the tradeoff.

    Typical latency: 10–30 seconds (one data download, N simulations).
    """
    start_ms, end_ms = _date_range_ms(body.days)

    log.info(
        "backtest.stat_arb_sweep",
        spot=body.spot_symbol,
        days=body.days,
        n_params=len(body.entry_z_values),
    )

    async with httpx.AsyncClient() as client:
        try:
            spot_candles, perp_candles = await asyncio.gather(
                data_fetcher.fetch_candles(
                    client, svc_settings, body.spot_symbol, body.interval, start_ms, end_ms
                ),
                data_fetcher.fetch_perp_candles(
                    client, svc_settings, body.perp_symbol, body.interval, start_ms, end_ms
                ),
            )
        except Exception as exc:
            log.error("backtest.sweep_data_failed", error=str(exc))
            return JSONResponse(
                status_code=502,
                content={"error": f"Data fetch failed: {str(exc)[:120]}"},
            )

    results = []
    for entry_z in sorted(set(body.entry_z_values)):
        r = engine.run_stat_arb(
            spot_candles=spot_candles,
            perp_candles=perp_candles,
            settings=svc_settings,
            window=body.window,
            entry_z=entry_z,
            exit_z=body.exit_z,
            fee_bps=body.fee_bps,
            capital_usd=body.capital_usd,
        )
        r.symbol = f"{body.spot_symbol}/{body.perp_symbol}"
        r.interval = body.interval
        results.append(_sweep_metrics(r, entry_z))

    return JSONResponse(content={
        "strategy": "stat_arb",
        "spot_symbol": body.spot_symbol,
        "perp_symbol": body.perp_symbol,
        "interval": body.interval,
        "days": body.days,
        "sweep_param": "entry_z",
        "results": results,
        "optimal_by_sharpe": (
            max(results, key=lambda x: x["sharpe_ratio"])["param_value"]
            if results else None
        ),
        "optimal_by_return": (
            max(results, key=lambda x: x["total_return_pct"])["param_value"]
            if results else None
        ),
        "note": "Optimal values are in-sample. Validate on out-of-sample data before deploying.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@router.post("/regime-split")
async def regime_split(body: RegimeSplitRequest) -> JSONResponse:
    """
    Run a backtest and split trade results by realised-volatility regime.

    Classifies each trade into one of three volatility terciles:
      low_vol  — calm market (ranging, low realised vol)
      mid_vol  — normal conditions
      high_vol — elevated volatility (volatile/crisis regimes)

    Use this to understand whether the strategy's edge is regime-dependent:
    - If P&L concentrates in low_vol: reduce sizing in volatile periods
    - If P&L is flat across regimes: strategy is regime-agnostic (good)
    - If P&L concentrates in high_vol: check whether signals are genuine

    Typical latency: 15–40 seconds.
    """
    if body.strategy not in ("funding_arb", "stat_arb"):
        return JSONResponse(
            status_code=400,
            content={"error": "strategy must be 'funding_arb' or 'stat_arb'"},
        )

    start_ms, end_ms = _date_range_ms(body.days)

    log.info(
        "backtest.regime_split",
        strategy=body.strategy,
        symbol=body.symbol,
        days=body.days,
    )

    async with httpx.AsyncClient() as client:
        try:
            if body.strategy == "funding_arb":
                spot_candles, funding_rates = await asyncio.gather(
                    data_fetcher.fetch_candles(
                        client, svc_settings, body.symbol, "1m", start_ms, end_ms
                    ),
                    data_fetcher.fetch_funding_rates(
                        client, svc_settings, body.symbol, start_ms, end_ms
                    ),
                )
                for c in spot_candles:
                    c["symbol"] = body.symbol
                result = engine.run_funding_arb(
                    spot_candles=spot_candles,
                    perp_candles=spot_candles,
                    funding_rates=funding_rates,
                    settings=svc_settings,
                    min_edge_bps=body.min_edge_bps,
                    fee_bps=body.fee_bps,
                    capital_usd=body.capital_usd,
                )
                vol_candles = spot_candles
                # 480-candle window = 8 hours of 1-minute bars
                vol_window = 480

            else:  # stat_arb
                spot_candles, perp_candles = await asyncio.gather(
                    data_fetcher.fetch_candles(
                        client, svc_settings, body.symbol, body.interval, start_ms, end_ms
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
                vol_candles = spot_candles
                # 24-candle window = 24 hours of hourly bars (or 24 of whatever interval)
                vol_window = 24

        except Exception as exc:
            log.error("backtest.regime_split_failed", error=str(exc))
            return JSONResponse(
                status_code=502,
                content={"error": f"Data fetch failed: {str(exc)[:120]}"},
            )

    # Compute rolling realised volatility
    vols = _rolling_realised_vol(vol_candles, vol_window)

    # Split trade log into vol terciles
    split = _classify_trades_by_vol(result.trade_log, vol_candles, vols)

    total = result.total_trades
    regime_stats = {}
    for regime_name, trades in split.items():
        stats = _aggregate(trades, body.capital_usd)
        stats["pct_of_total_trades"] = round(stats["count"] / total * 100, 1) if total else 0.0
        regime_stats[regime_name] = stats

    log.info(
        "backtest.regime_split_done",
        strategy=body.strategy,
        total_trades=total,
        low_vol=len(split["low_vol"]),
        mid_vol=len(split["mid_vol"]),
        high_vol=len(split["high_vol"]),
    )

    return JSONResponse(content={
        "strategy": body.strategy,
        "symbol": body.symbol,
        "days": body.days,
        "total_trades": total,
        "overall_metrics": {
            "win_rate": result.win_rate,
            "total_return_pct": result.total_return_pct,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "net_pnl_usd": result.net_pnl_usd,
        },
        "regime_split": regime_stats,
        "interpretation": {
            "low_vol": "Calm/ranging market — low realised volatility (bottom tercile)",
            "mid_vol": "Normal market conditions (middle tercile)",
            "high_vol": "Elevated volatility — volatile/crisis conditions (top tercile)",
        },
        "sizing_guidance": (
            "If low_vol win_rate >> high_vol win_rate: reduce position size in volatile regimes. "
            "If results are uniform across regimes: strategy is regime-agnostic."
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
