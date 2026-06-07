"""
Kelly Criterion position sizing.

Kelly fraction = W - (1 - W) / R
  W = win rate  (fraction of trades that are profitable)
  R = win/loss ratio  (average win USD / average loss USD)

Fractional Kelly: multiply by kelly_multiplier to control variance.
  - Full Kelly  (1.0): maximises geometric growth; extreme drawdowns
  - Half Kelly  (0.5): ~75% of growth rate; practical for live trading
  - Quarter Kelly (0.25): conservative; similar to fixed-fractional

Position cap: max_position_pct is a hard safety cap independent of Kelly.
Kelly with small samples can suggest >50% of capital in one trade — the cap
prevents catastrophic over-sizing while live stats are still thin.

Redis keys read by get_strategy_kelly_fraction():
  edge:win_rate:{strategy}       — rolling win rate (written by edge_monitor)
  strategy:avg_win:{strategy}    — rolling average win in USD
  strategy:avg_loss:{strategy}   — rolling average loss in USD

These are written by the executor consumer after each trade settlement.

Lives in mezna_shared so both the risk and executor services use one
implementation (the executor sizes live orders; risk/backtest report sizing).
"""

import math
import structlog
from typing import Optional

log = structlog.get_logger()

# Hard bounds — Kelly can produce absurd numbers with thin data
_KELLY_MIN = 0.005   # 0.5% floor: below this the edge isn't worth trading
_KELLY_MAX = 0.25    # 25% ceiling: never risk more than 25% of capital per trade


def compute_kelly_fraction(
    win_rate: float,
    avg_win_usd: float,
    avg_loss_usd: float,
    kelly_multiplier: float = 0.5,
) -> float:
    """
    Compute the fractional Kelly position-sizing fraction.

    Returns 0.0 when there is no positive mathematical edge.
    Clamps output to [_KELLY_MIN, _KELLY_MAX].
    """
    if not (0.0 < win_rate < 1.0):
        return 0.0
    if avg_win_usd <= 0.0 or avg_loss_usd <= 0.0:
        return 0.0

    loss_rate      = 1.0 - win_rate
    win_loss_ratio = avg_win_usd / avg_loss_usd          # R

    full_kelly = win_rate - (loss_rate / win_loss_ratio)  # Kelly formula

    if full_kelly <= 0.0:
        log.debug(
            "kelly.no_edge",
            win_rate=round(win_rate, 4),
            rr=round(win_loss_ratio, 4),
            full_kelly=round(full_kelly, 6),
        )
        return 0.0

    fractional = min(max(full_kelly * kelly_multiplier, _KELLY_MIN), _KELLY_MAX)

    log.debug(
        "kelly.computed",
        win_rate=round(win_rate, 4),
        rr=round(win_loss_ratio, 4),
        full_kelly=round(full_kelly, 4),
        fractional=round(fractional, 4),
        multiplier=kelly_multiplier,
    )
    return round(fractional, 6)


def kelly_position_usd(
    capital_usd: float,
    kelly_fraction: float,
    max_position_pct: float = 0.05,
    min_position_usd: float = 10.0,
) -> float:
    """
    Convert a Kelly fraction to a dollar position size.

    Args:
        capital_usd:     Total account equity in USD
        kelly_fraction:  Output of compute_kelly_fraction()
        max_position_pct: Hard cap as fraction of capital (default 5%)
        min_position_usd: Floor — skip trade if position would be below this

    Returns position in USD, or 0.0 if below minimum.
    """
    if kelly_fraction <= 0.0 or capital_usd <= 0.0:
        return 0.0

    position = min(capital_usd * kelly_fraction, capital_usd * max_position_pct)
    return round(position, 2) if position >= min_position_usd else 0.0


def estimate_kelly_from_returns(
    trade_returns: list[float],
    kelly_multiplier: float = 0.5,
) -> dict:
    """
    Derive Kelly parameters from a historical list of trade net P&L (USD).

    Useful for the backtest service to report optimal sizing alongside
    simulation metrics.
    """
    if not trade_returns:
        return {
            "win_rate": 0.0,
            "avg_win_usd": 0.0,
            "avg_loss_usd": 0.0,
            "win_loss_ratio": 0.0,
            "kelly_fraction": 0.0,
            "kelly_multiplier": kelly_multiplier,
            "sample_size": 0,
            "expected_log_growth": 0.0,
        }

    wins   = [r for r in trade_returns if r > 0]
    losses = [abs(r) for r in trade_returns if r <= 0]

    win_rate = len(wins) / len(trade_returns)
    avg_win  = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 1e-6

    fraction = compute_kelly_fraction(win_rate, avg_win, avg_loss, kelly_multiplier)

    # Theoretical expected log-growth per trade
    if fraction > 0 and win_rate > 0:
        rr = avg_win / avg_loss
        growth = (
            win_rate * math.log(1.0 + fraction * rr)
            + (1.0 - win_rate) * math.log(max(1.0 - fraction, 1e-9))
        )
    else:
        growth = 0.0

    return {
        "win_rate":           round(win_rate, 4),
        "avg_win_usd":        round(avg_win, 4),
        "avg_loss_usd":       round(avg_loss, 4),
        "win_loss_ratio":     round(avg_win / avg_loss, 4) if avg_loss > 0 else 0.0,
        "kelly_fraction":     fraction,
        "kelly_multiplier":   kelly_multiplier,
        "sample_size":        len(trade_returns),
        "expected_log_growth": round(growth, 6),
    }


async def get_strategy_kelly_fraction(
    redis,
    strategy: str,
    kelly_multiplier: float = 0.5,
    fallback_fraction: float = 0.01,
) -> float:
    """
    Read live Kelly inputs from Redis and compute the current fraction.

    Falls back to fallback_fraction when stats are unavailable
    (e.g., early in paper trading before enough data has accumulated).
    """
    try:
        win_rate_raw = await redis.get(f"edge:win_rate:{strategy}")
        avg_win_raw  = await redis.get(f"strategy:avg_win:{strategy}")
        avg_loss_raw = await redis.get(f"strategy:avg_loss:{strategy}")

        if not (win_rate_raw and avg_win_raw and avg_loss_raw):
            return fallback_fraction

        win_rate = float(win_rate_raw)
        avg_win  = float(avg_win_raw)
        avg_loss = float(avg_loss_raw)

        fraction = compute_kelly_fraction(win_rate, avg_win, avg_loss, kelly_multiplier)
        return fraction if fraction > 0 else fallback_fraction

    except Exception as exc:
        log.warning("kelly.redis_read_failed", strategy=strategy, error=str(exc))
        return fallback_fraction
