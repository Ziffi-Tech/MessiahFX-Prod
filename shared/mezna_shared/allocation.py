"""
Multi-strategy capital allocation — turn per-strategy returns into capital weights.

Phase 2 answers "which strategies are good"; this answers "how much capital each
should get". Methods, simplest → richest:
  - equal_weight       : 1/N baseline
  - inverse_vol        : weight ∝ 1/σ (down-weight the volatile)
  - risk_parity (ERC)  : each strategy contributes equal portfolio risk
  - max_sharpe         : tangency portfolio (Σ⁻¹μ), long-only, renormalised

Pure Python — no numpy/scipy/riskfolio. Strategy counts are tiny (≤ a handful), so
hand-rolled covariance + Gauss-Jordan inverse are exact and dependency-free, keeping
mezna_shared (baked into every service image) lightweight. riskfolio-lib remains an
option only if advanced methods (HRP, CVaR) are ever needed.

Inputs are DATE-ALIGNED daily realised-P&L series per strategy (equal length; the
caller fills non-trading days with 0). Allocation is over the strategies with
usable history (≥2 points and non-zero variance); the rest get weight 0.
"""

from __future__ import annotations

from statistics import fmean, stdev


def _stdev(xs: list[float]) -> float:
    return stdev(xs) if len(xs) >= 2 else 0.0


def _cov(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    mx, my = fmean(xs[:n]), fmean(ys[:n])
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)


def _cov_matrix(series: list[list[float]]) -> list[list[float]]:
    n = len(series)
    return [[_cov(series[i], series[j]) for j in range(n)] for i in range(n)]


def _matvec(m: list[list[float]], v: list[float]) -> list[float]:
    return [sum(m[i][j] * v[j] for j in range(len(v))) for i in range(len(m))]


def _quad(v: list[float], m: list[list[float]]) -> float:
    return sum(v[i] * mv for i, mv in enumerate(_matvec(m, v)))


def _invert(matrix: list[list[float]]) -> list[list[float]] | None:
    """Gauss-Jordan inverse with partial pivoting; None if singular."""
    n = len(matrix)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        pv = a[col][col]
        a[col] = [x / pv for x in a[col]]
        for r in range(n):
            if r != col and a[r][col] != 0.0:
                factor = a[r][col]
                a[r] = [a[r][k] - factor * a[col][k] for k in range(2 * n)]
    return [row[n:] for row in a]


def _normalize(weights: list[float]) -> list[float]:
    s = sum(weights)
    if s <= 0:
        n = len(weights)
        return [1.0 / n] * n if n else []
    return [w / s for w in weights]


# ── Allocation methods (operate on the usable subset; return aligned weights) ──

def _inverse_vol(series: list[list[float]]) -> list[float]:
    inv = [1.0 / _stdev(s) if _stdev(s) > 0 else 0.0 for s in series]
    return _normalize(inv)


def _risk_parity(series: list[list[float]], iters: int = 1000, tol: float = 1e-6) -> list[float]:
    """Equal-risk-contribution via the standard multiplicative fixed point."""
    n = len(series)
    cov = _cov_matrix(series)
    w = _inverse_vol(series)  # warm start
    for _ in range(iters):
        mrc = _matvec(cov, w)               # marginal risk contributions
        rc = [w[i] * mrc[i] for i in range(n)]
        total = sum(rc)
        if total <= 0:
            break
        target = total / n
        w = _normalize([w[i] * (target / rc[i]) if rc[i] > 0 else w[i] for i in range(n)])
        if max(abs(rc[i] - target) for i in range(n)) / total < tol:
            break
    return w


def _max_sharpe(series: list[list[float]]) -> list[float]:
    """Tangency portfolio Σ⁻¹μ, long-only (clip negatives) + renormalise."""
    cov = _cov_matrix(series)
    cinv = _invert(cov)
    if cinv is None:
        return _inverse_vol(series)
    mu = [fmean(s) for s in series]
    raw = _matvec(cinv, mu)
    clipped = [max(0.0, x) for x in raw]
    if sum(clipped) <= 0:
        return [1.0 / len(series)] * len(series)  # all non-positive edge → equal
    return _normalize(clipped)


def _risk_contributions(weights: list[float], cov: list[list[float]]) -> list[float]:
    total = _quad(weights, cov)
    if total <= 0:
        n = len(weights)
        return [1.0 / n] * n if n else []
    mrc = _matvec(cov, weights)
    return [weights[i] * mrc[i] / total for i in range(len(weights))]


_METHODS = ("equal_weight", "inverse_vol", "risk_parity", "max_sharpe")


def allocate(series_by_name: dict[str, list[float]], method: str = "risk_parity", capital: float = 0.0) -> dict:
    """
    Allocate capital across strategies. Returns weights, per-strategy stats
    (daily mean P&L, daily vol, risk contribution), and the capital split.
    """
    if method not in _METHODS:
        method = "risk_parity"

    names = list(series_by_name)
    usable = [n for n in names if len(series_by_name[n]) >= 2 and _stdev(series_by_name[n]) > 0]

    weights = {n: 0.0 for n in names}

    if method == "equal_weight" or not usable:
        pool = usable or names
        for n in pool:
            weights[n] = 1.0 / len(pool) if pool else 0.0
    else:
        series = [series_by_name[n] for n in usable]
        if method == "inverse_vol":
            w = _inverse_vol(series)
        elif method == "max_sharpe":
            w = _max_sharpe(series)
        else:
            w = _risk_parity(series)
        for i, n in enumerate(usable):
            weights[n] = w[i]

    # Risk contributions over the usable subset (display).
    rc_by_name = {n: None for n in names}
    if usable:
        sub_cov = _cov_matrix([series_by_name[n] for n in usable])
        sub_w = [weights[n] for n in usable]
        for n, rc in zip(usable, _risk_contributions(sub_w, sub_cov)):
            rc_by_name[n] = round(rc, 6)

    strategies = []
    for n in names:
        s = series_by_name[n]
        strategies.append({
            "strategy_type": n,
            "weight": round(weights[n], 6),
            "capital": round(weights[n] * capital, 2),
            "daily_mean_pnl": round(fmean(s), 6) if s else 0.0,
            "daily_vol": round(_stdev(s), 6),
            "risk_contribution": rc_by_name[n],
            "usable": n in usable,
        })
    strategies.sort(key=lambda x: x["weight"], reverse=True)

    return {
        "method": method,
        "capital": capital,
        "usable_count": len(usable),
        # Full precision so weights sum to exactly 1; display rounding is per-strategy.
        "weights": {n: weights[n] for n in names},
        "strategies": strategies,
    }
