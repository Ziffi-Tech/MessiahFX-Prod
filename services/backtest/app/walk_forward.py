"""
Walk-forward analysis (WFA) — out-of-sample validation against overfitting.

Roll an in-sample (IS) window forward; optimise parameters on IS, then test the
chosen parameters on the *next, unseen* out-of-sample (OOS) window. Repeat. The
OOS performance — not the IS best — is what predicts live behaviour.

Built on the existing engine (engine.run_stat_arb) rather than vectorbt: it reuses
the proven, persisted-OHLCV path and avoids vectorbt's heavy dep + paradigm
mismatch (see docs/decisions/0001). vectorbt remains optional as a vectorised
accelerator for very large parameter grids.

Two pieces are pure (and unit-tested): wfa_windows (the rolling split) and
summarize_walk_forward (OOS aggregate + walk-forward efficiency + parameter
stability + verdict). walk_forward_stat_arb wires them to the engine.
"""

from __future__ import annotations

from statistics import median
from typing import Any

from . import engine
from .config import Settings


def wfa_windows(n: int, is_size: int, oos_size: int, step: int) -> list[tuple[int, int, int, int]]:
    """
    Rolling IS/OOS index windows over n observations.

    Returns (is_start, is_end, oos_start, oos_end) tuples; oos_start == is_end
    (OOS immediately follows IS), advancing by `step` until OOS runs past the end.
    """
    if min(is_size, oos_size, step) <= 0:
        raise ValueError("is_size, oos_size and step must all be > 0")
    windows: list[tuple[int, int, int, int]] = []
    is_start = 0
    while is_start + is_size + oos_size <= n:
        is_end = is_start + is_size
        oos_end = is_end + oos_size
        windows.append((is_start, is_end, is_end, oos_end))
        is_start += step
    return windows


def _param_stability(folds: list[dict]) -> dict:
    """Per-parameter consistency across folds: distinct values + modal share."""
    if not folds:
        return {}
    keys = folds[0].get("params", {}).keys()
    out: dict[str, Any] = {}
    for key in keys:
        values = [f["params"].get(key) for f in folds]
        counts: dict[Any, int] = {}
        for v in values:
            counts[v] = counts.get(v, 0) + 1
        mode_value, mode_count = max(counts.items(), key=lambda kv: kv[1])
        out[key] = {
            "distinct": len(counts),
            "mode": mode_value,
            "mode_fraction": round(mode_count / len(values), 4),
        }
    return out


def summarize_walk_forward(folds: list[dict]) -> dict:
    """
    Aggregate OOS performance + robustness signals from per-fold results.

    walk_forward_efficiency = mean(OOS Sharpe) / mean(IS Sharpe): ~1 means OOS
    holds up; well below 1 signals overfitting. Verdict combines median OOS Sharpe,
    the fraction of profitable OOS folds, and WFE.
    """
    if not folds:
        return {
            "folds": 0, "verdict": "insufficient_data",
            "median_oos_sharpe": None, "mean_oos_sharpe": None,
            "walk_forward_efficiency": None, "positive_fold_fraction": None,
            "total_oos_net_pnl": 0.0, "total_oos_trades": 0,
            "parameter_stability": {},
        }

    oos_sharpes = [float(f.get("oos_sharpe", 0) or 0) for f in folds]
    is_sharpes = [float(f.get("is_sharpe", 0) or 0) for f in folds]
    oos_pnls = [float(f.get("oos_net_pnl", 0) or 0) for f in folds]

    mean_oos = sum(oos_sharpes) / len(oos_sharpes)
    med_oos = median(oos_sharpes)
    mean_is = sum(is_sharpes) / len(is_sharpes)
    wfe = round(mean_oos / mean_is, 4) if mean_is > 0 else None
    positive_fraction = round(sum(1 for p in oos_pnls if p > 0) / len(oos_pnls), 4)

    if med_oos > 0 and positive_fraction >= 0.5 and (wfe is None or wfe >= 0.5):
        verdict = "robust"
    elif med_oos <= 0 or positive_fraction < 0.4 or (wfe is not None and wfe < 0.3):
        verdict = "overfit"
    else:
        verdict = "marginal"

    return {
        "folds": len(folds),
        "verdict": verdict,
        "median_oos_sharpe": round(med_oos, 4),
        "mean_oos_sharpe": round(mean_oos, 4),
        "mean_is_sharpe": round(mean_is, 4),
        "walk_forward_efficiency": wfe,
        "positive_fold_fraction": positive_fraction,
        "total_oos_net_pnl": round(sum(oos_pnls), 4),
        "total_oos_trades": sum(int(f.get("oos_trades", 0) or 0) for f in folds),
        "parameter_stability": _param_stability(folds),
    }


def _align(spot: list[dict], perp: list[dict]) -> tuple[list[dict], list[dict]]:
    """Intersect spot/perp candles by timestamp so index slicing stays aligned."""
    perp_by_ts = {c["ts"]: c for c in perp}
    a_spot = [c for c in spot if c["ts"] in perp_by_ts]
    a_perp = [perp_by_ts[c["ts"]] for c in a_spot]
    return a_spot, a_perp


def walk_forward_stat_arb(
    spot: list[dict],
    perp: list[dict],
    settings: Settings,
    *,
    window_grid: list[int],
    entry_z_grid: list[float],
    exit_z: float,
    fee_bps: float,
    capital_usd: float,
    is_size: int,
    oos_size: int,
    step: int,
) -> dict:
    """
    Walk-forward stat-arb: optimise (window, entry_z) on each IS window by Sharpe,
    test on the following OOS window. Returns per-fold results + the summary.
    """
    a_spot, a_perp = _align(spot, perp)
    n = len(a_spot)
    windows = wfa_windows(n, is_size, oos_size, step)

    folds: list[dict] = []
    for is_start, is_end, oos_start, oos_end in windows:
        is_spot, is_perp = a_spot[is_start:is_end], a_perp[is_start:is_end]

        # In-sample optimisation: highest IS Sharpe over the grid.
        best: dict | None = None
        for w in window_grid:
            for ez in entry_z_grid:
                r = engine.run_stat_arb(
                    is_spot, is_perp, settings,
                    window=w, entry_z=ez, exit_z=exit_z, fee_bps=fee_bps, capital_usd=capital_usd,
                )
                if best is None or r.sharpe_ratio > best["is_sharpe"]:
                    best = {"window": w, "entry_z": ez, "is_sharpe": r.sharpe_ratio, "is_trades": r.total_trades}

        # Out-of-sample test with the IS-best parameters.
        oos_spot, oos_perp = a_spot[oos_start:oos_end], a_perp[oos_start:oos_end]
        oos = engine.run_stat_arb(
            oos_spot, oos_perp, settings,
            window=best["window"], entry_z=best["entry_z"], exit_z=exit_z,
            fee_bps=fee_bps, capital_usd=capital_usd,
        )

        folds.append({
            "params": {"window": best["window"], "entry_z": best["entry_z"]},
            "is_sharpe": best["is_sharpe"],
            "is_trades": best["is_trades"],
            "oos_sharpe": oos.sharpe_ratio,
            "oos_net_pnl": oos.net_pnl_usd,
            "oos_trades": oos.total_trades,
            "oos_win_rate": oos.win_rate,
            "oos_max_drawdown_pct": oos.max_drawdown_pct,
            "oos_start_dt": oos.start_dt,
            "oos_end_dt": oos.end_dt,
        })

    return {
        "aligned_candles": n,
        "windows": len(windows),
        "folds": folds,
        "summary": summarize_walk_forward(folds),
    }
