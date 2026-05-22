"""
Backtest simulation engine.

Runs historical simulations of funding_arb and stat_arb strategies
against downloaded OHLCV and funding rate data.

Simulation model:
  - Fills at close price of each candle (conservative — no look-ahead)
  - Taker fee applied on entry and exit
  - No partial fills, no slippage model (add in future)
  - Position sizing: fixed USD amount per trade (settings.DEFAULT_POSITION_PCT × capital)

Metrics returned:
  total_trades, winning_trades, losing_trades, win_rate
  total_pnl_usd, total_fees_usd, net_pnl_usd
  max_drawdown_pct, sharpe_ratio (annualised, daily returns)
  avg_hold_candles, trade_log (list of individual trades)

Funding Arb simulation:
  Entry: when funding_rate_bps > MIN_EDGE + fee_cost
  Position: long spot, short perp (delta-neutral)
  Income: funding payment received every 8h (at funding timestamps)
  Exit: after one funding period (8h) — simplified "hold to next payment"
  P&L: funding_received - entry_fee - exit_fee - spread_cost

Stat Arb simulation:
  Entry: |z_score| > ENTRY_Z (price spread between spot and perp)
  Direction: sell overpriced, buy underpriced
  Exit: |z_score| < EXIT_Z (spread reverts to mean)
  P&L: (entry_spread - exit_spread) × qty - fees
"""

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog

from .config import Settings

log = structlog.get_logger()


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    entry_ts: str
    exit_ts: str
    symbol: str
    strategy: str
    side: str                # "long_spot_short_perp" or "sell_spread" or "buy_spread"
    entry_price: float
    exit_price: float
    quantity: float
    pnl_usd: float
    fee_usd: float
    net_pnl_usd: float
    hold_candles: int


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    interval: str
    start_dt: str
    end_dt: str
    capital_usd: float
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl_usd: float = 0.0
    total_fees_usd: float = 0.0
    net_pnl_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    avg_hold_candles: float = 0.0
    total_return_pct: float = 0.0
    trade_log: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


# ── Metrics helpers ───────────────────────────────────────────────────────────

def _compute_metrics(
    trade_records: list[TradeRecord],
    capital_usd: float,
    candles: list[dict],
    settings: Settings,
) -> dict:
    """Compute aggregate metrics from a list of trade records."""
    if not trade_records:
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "total_pnl_usd": 0.0,
            "total_fees_usd": 0.0,
            "net_pnl_usd": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "avg_hold_candles": 0.0,
            "total_return_pct": 0.0,
        }

    net_pnls = [t.net_pnl_usd for t in trade_records]
    total_net = sum(net_pnls)
    winning = [p for p in net_pnls if p > 0]
    losing  = [p for p in net_pnls if p <= 0]

    # Equity curve — running sum of P&L starting from capital
    equity = capital_usd
    peak = capital_usd
    max_dd = 0.0
    daily_returns = []
    prev_equity = capital_usd

    for pnl in net_pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        daily_returns.append((equity - prev_equity) / prev_equity if prev_equity > 0 else 0.0)
        prev_equity = equity

    # Sharpe ratio (annualised, assuming 252 trading days, simplified)
    if len(daily_returns) > 1:
        mu = np.mean(daily_returns)
        sigma = np.std(daily_returns, ddof=1)
        sharpe = (mu / sigma * math.sqrt(252)) if sigma > 0 else 0.0
    else:
        sharpe = 0.0

    avg_hold = sum(t.hold_candles for t in trade_records) / len(trade_records)
    total_return_pct = (total_net / capital_usd) * 100 if capital_usd > 0 else 0.0

    return {
        "total_trades": len(trade_records),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": round(len(winning) / len(trade_records), 4) if trade_records else 0.0,
        "total_pnl_usd": round(sum(t.pnl_usd for t in trade_records), 4),
        "total_fees_usd": round(sum(t.fee_usd for t in trade_records), 4),
        "net_pnl_usd": round(total_net, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "sharpe_ratio": round(sharpe, 4),
        "avg_hold_candles": round(avg_hold, 2),
        "total_return_pct": round(total_return_pct, 4),
    }


def _build_equity_curve(
    trade_records: list[TradeRecord], capital_usd: float
) -> list[dict]:
    equity = capital_usd
    curve = []
    for t in trade_records:
        equity += t.net_pnl_usd
        curve.append({
            "ts": t.exit_ts,
            "equity_usd": round(equity, 4),
            "trade_pnl": round(t.net_pnl_usd, 4),
        })
    return curve


# ── Funding arb simulation ────────────────────────────────────────────────────

def run_funding_arb(
    spot_candles: list[dict],
    perp_candles: list[dict],
    funding_rates: list[dict],
    settings: Settings,
    *,
    min_edge_bps: float,
    fee_bps: float,
    capital_usd: float,
) -> BacktestResult:
    """
    Simulate funding rate arbitrage.

    Strategy: when funding_rate_bps > min_edge_bps + 2×fee_bps,
    enter long spot / short perp, collect funding payment,
    exit after next funding period.

    Simplified model:
    - Entry at close price of candle just before funding time
    - Exit at close price of next funding period candle
    - P&L = funding_received (as % of notional) - round-trip fees
    """
    position_usd = capital_usd * settings.DEFAULT_POSITION_PCT

    # Build index of spot candles by timestamp for O(1) lookup
    spot_by_ts = {c["ts"]: c for c in spot_candles}
    perp_by_ts  = {c["ts"]: c for c in perp_candles}

    # Sort funding rates by time
    rates = sorted(funding_rates, key=lambda r: r["ts"])

    trade_records: list[TradeRecord] = []
    in_position = False
    entry_record: dict = {}

    for i, rate in enumerate(rates):
        rate_bps = rate["rate_bps"]
        round_trip_fee_bps = 2 * fee_bps  # entry + exit

        if not in_position and rate_bps > (min_edge_bps + round_trip_fee_bps):
            # Enter — find spot candle nearest to funding time
            spot_c = spot_by_ts.get(rate["ts"])
            if not spot_c:
                continue  # No candle data at this time
            entry_price = spot_c["close"]
            quantity = position_usd / entry_price
            entry_fee = position_usd * (fee_bps / 10_000) * 2  # both legs

            in_position = True
            entry_record = {
                "ts": rate["ts_dt"],
                "price": entry_price,
                "qty": quantity,
                "fee": entry_fee,
                "funding_rate_bps": rate_bps,
                "candle_idx": i,
            }

        elif in_position and i > entry_record.get("candle_idx", i):
            # Exit after next funding period
            exit_rate = rates[i]
            spot_c = spot_by_ts.get(exit_rate["ts"])
            if not spot_c:
                continue

            exit_price = spot_c["close"]
            quantity = entry_record["qty"]
            exit_fee = position_usd * (fee_bps / 10_000) * 2

            # Funding income = rate × notional (received by short perp)
            funding_income = (entry_record["funding_rate_bps"] / 10_000) * position_usd
            total_fee = entry_record["fee"] + exit_fee

            # Spot price change (long spot exposure)
            price_pnl = (exit_price - entry_record["price"]) / entry_record["price"] * position_usd

            gross_pnl = funding_income + price_pnl
            net_pnl = gross_pnl - total_fee

            trade_records.append(TradeRecord(
                entry_ts=entry_record["ts"],
                exit_ts=exit_rate["ts_dt"],
                symbol=spot_candles[0].get("symbol", ""),
                strategy="funding_arb",
                side="long_spot_short_perp",
                entry_price=entry_record["price"],
                exit_price=exit_price,
                quantity=quantity,
                pnl_usd=round(gross_pnl, 6),
                fee_usd=round(total_fee, 6),
                net_pnl_usd=round(net_pnl, 6),
                hold_candles=i - entry_record["candle_idx"],
            ))
            in_position = False

    start_dt = spot_candles[0]["ts_dt"] if spot_candles else ""
    end_dt = spot_candles[-1]["ts_dt"] if spot_candles else ""

    metrics = _compute_metrics(trade_records, capital_usd, spot_candles, settings)
    equity_curve = _build_equity_curve(trade_records, capital_usd)

    result = BacktestResult(
        strategy="funding_arb",
        symbol=spot_candles[0].get("symbol", "") if spot_candles else "",
        interval="8h_funding",
        start_dt=start_dt,
        end_dt=end_dt,
        capital_usd=capital_usd,
        trade_log=[vars(t) for t in trade_records],
        equity_curve=equity_curve,
        params={
            "min_edge_bps": min_edge_bps,
            "fee_bps": fee_bps,
            "position_usd": position_usd,
        },
        **metrics,
    )
    log.info(
        "backtest.funding_arb_done",
        trades=result.total_trades,
        net_pnl=result.net_pnl_usd,
        sharpe=result.sharpe_ratio,
    )
    return result


# ── Stat arb simulation ───────────────────────────────────────────────────────

def _rolling_z(values: list[float], window: int) -> list[float | None]:
    """Return rolling z-score array; None for insufficient history."""
    out: list[float | None] = [None] * window
    for i in range(window, len(values)):
        window_vals = values[i - window: i]
        mu = np.mean(window_vals)
        sigma = np.std(window_vals, ddof=1)
        out.append(float((values[i] - mu) / sigma) if sigma > 0 else 0.0)
    return out


def run_stat_arb(
    spot_candles: list[dict],
    perp_candles: list[dict],
    settings: Settings,
    *,
    window: int,
    entry_z: float,
    exit_z: float,
    fee_bps: float,
    capital_usd: float,
) -> BacktestResult:
    """
    Simulate statistical arbitrage on spot vs perp spread.

    Entry: |z_score| > entry_z → sell overpriced, buy underpriced
    Exit:  |z_score| < exit_z  → close both legs
    P&L: (entry_spread - exit_spread) × position_usd / avg_price - round_trip_fees
    """
    position_usd = capital_usd * settings.DEFAULT_POSITION_PCT

    # Align by timestamp
    perp_by_ts = {c["ts"]: c for c in perp_candles}
    aligned_spot = []
    aligned_perp = []

    for c in spot_candles:
        perp_c = perp_by_ts.get(c["ts"])
        if perp_c:
            aligned_spot.append(c)
            aligned_perp.append(perp_c)

    if len(aligned_spot) < window + 10:
        log.warning("backtest.insufficient_data", required=window + 10, got=len(aligned_spot))
        return BacktestResult(
            strategy="stat_arb",
            symbol="",
            interval="",
            start_dt="",
            end_dt="",
            capital_usd=capital_usd,
        )

    spreads = [s["mid"] - p["mid"] for s, p in zip(aligned_spot, aligned_perp)]
    z_scores = _rolling_z(spreads, window)

    trade_records: list[TradeRecord] = []
    in_position = False
    entry_idx = 0
    entry_z_sign = 0  # +1 or -1

    for i, (z, s_c, p_c) in enumerate(zip(z_scores, aligned_spot, aligned_perp)):
        if z is None:
            continue

        if not in_position and abs(z) > entry_z:
            entry_z_sign = 1 if z > 0 else -1
            entry_spread = spreads[i]
            entry_price = s_c["mid"]
            quantity = position_usd / entry_price
            entry_fee = position_usd * (fee_bps / 10_000) * 2  # both legs

            in_position = True
            entry_idx = i
            entry_data = {
                "ts": s_c["ts_dt"],
                "spread": entry_spread,
                "price": entry_price,
                "qty": quantity,
                "fee": entry_fee,
                "z": z,
            }

        elif in_position and abs(z) < exit_z:
            exit_spread = spreads[i]
            # P&L: spread converged → the trade that sold the expensive leg profits
            spread_change = entry_data["spread"] - exit_spread  # positive if spread narrows
            pnl = entry_z_sign * spread_change / abs(entry_data["price"]) * position_usd
            exit_fee = position_usd * (fee_bps / 10_000) * 2
            total_fee = entry_data["fee"] + exit_fee
            net_pnl = pnl - total_fee

            trade_records.append(TradeRecord(
                entry_ts=entry_data["ts"],
                exit_ts=s_c["ts_dt"],
                symbol="",
                strategy="stat_arb",
                side="sell_spread" if entry_z_sign > 0 else "buy_spread",
                entry_price=entry_data["price"],
                exit_price=s_c["mid"],
                quantity=entry_data["qty"],
                pnl_usd=round(pnl, 6),
                fee_usd=round(total_fee, 6),
                net_pnl_usd=round(net_pnl, 6),
                hold_candles=i - entry_idx,
            ))
            in_position = False

    start_dt = aligned_spot[0]["ts_dt"] if aligned_spot else ""
    end_dt = aligned_spot[-1]["ts_dt"] if aligned_spot else ""

    metrics = _compute_metrics(trade_records, capital_usd, aligned_spot, settings)
    equity_curve = _build_equity_curve(trade_records, capital_usd)

    result = BacktestResult(
        strategy="stat_arb",
        symbol="",
        interval="",
        start_dt=start_dt,
        end_dt=end_dt,
        capital_usd=capital_usd,
        trade_log=[vars(t) for t in trade_records],
        equity_curve=equity_curve,
        params={
            "window": window,
            "entry_z": entry_z,
            "exit_z": exit_z,
            "fee_bps": fee_bps,
            "position_usd": position_usd,
        },
        **metrics,
    )
    log.info(
        "backtest.stat_arb_done",
        trades=result.total_trades,
        net_pnl=result.net_pnl_usd,
        sharpe=result.sharpe_ratio,
    )
    return result
