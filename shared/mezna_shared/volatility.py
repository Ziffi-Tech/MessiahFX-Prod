"""
Volatility forecasting + vol-aware position sizing.

Trade smaller when volatility is high, larger when it's low — so risk per trade
stays roughly constant as the market regime shifts. Allocation (allocation.py)
splits capital *across* strategies; this sizes *within* a symbol over time.

Forecasters (pure Python — no `arch`/scipy/numpy):
  - EWMA (RiskMetrics): σ²_t = λσ²_{t-1} + (1-λ)r²_{t-1}
  - GARCH(1,1): σ²_t = ω + α·r²_{t-1} + β·σ²_{t-1}, fit by variance-targeting
    (ω = (1-α-β)·sample var) + a small grid MLE over (α, β). Real GARCH dynamics
    without an optimiser dependency.

Sizing:
  - vol_target_multiplier: target_vol / forecast_vol, clamped (absolute targeting)
  - relative_sizing_multiplier: long-run vol / recent vol, clamped (unit-free —
    used live in the executor, needs no absolute target).
"""

from __future__ import annotations

import math
from statistics import pstdev


def returns_from_prices(prices: list[float]) -> list[float]:
    """Simple returns from a price series (skips non-positive prior prices)."""
    out: list[float] = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        if prev > 0:
            out.append((prices[i] - prev) / prev)
    return out


def ewma_vol(returns: list[float], lam: float = 0.94) -> float:
    """EWMA volatility forecast (one-step). 0 for an empty series."""
    if not returns:
        return 0.0
    var = returns[0] ** 2
    for r in returns[1:]:
        var = lam * var + (1.0 - lam) * r * r
    return math.sqrt(max(var, 0.0))


def _uncond_var(returns: list[float]) -> float:
    return sum(r * r for r in returns) / len(returns) if returns else 0.0


def _garch_loglik(returns: list[float], omega: float, alpha: float, beta: float) -> float:
    var = _uncond_var(returns) or 1e-12
    ll = 0.0
    for r in returns:
        if var < 1e-18:
            var = 1e-18
        ll += -0.5 * (math.log(var) + r * r / var)
        var = omega + alpha * r * r + beta * var
    return ll


def garch11_fit(returns: list[float]) -> dict | None:
    """
    Fit GARCH(1,1) by variance targeting + a coarse grid MLE over (α, β).
    Returns {omega, alpha, beta, loglik} or None if there's too little data.
    """
    n = len(returns)
    if n < 20:
        return None
    uncond = _uncond_var(returns)
    if uncond <= 0:
        return None

    best: tuple[float, float, float, float] | None = None
    alphas = [a / 100 for a in range(1, 31, 2)]    # 0.01 … 0.29
    betas = [b / 100 for b in range(50, 99, 3)]     # 0.50 … 0.98
    for alpha in alphas:
        for beta in betas:
            if alpha + beta >= 0.999:
                continue
            omega = (1.0 - alpha - beta) * uncond
            ll = _garch_loglik(returns, omega, alpha, beta)
            if best is None or ll > best[0]:
                best = (ll, omega, alpha, beta)

    if best is None:
        return None
    return {"omega": best[1], "alpha": best[2], "beta": best[3], "loglik": round(best[0], 4)}


def garch11_forecast(returns: list[float], params: dict) -> float:
    """One-step-ahead GARCH(1,1) volatility forecast given fitted params."""
    omega, alpha, beta = params["omega"], params["alpha"], params["beta"]
    var = _uncond_var(returns)
    for r in returns:
        var = omega + alpha * r * r + beta * var
    return math.sqrt(max(var, 0.0))


def forecast_vol(returns: list[float], method: str = "ewma", lam: float = 0.94) -> tuple[float, dict | None]:
    """Forecast one-step vol. Returns (vol, garch_params|None). GARCH falls back to EWMA."""
    if method == "garch":
        params = garch11_fit(returns)
        if params:
            return garch11_forecast(returns, params), params
    return ewma_vol(returns, lam), None


def annualize(vol_per_period: float, periods_per_year: float) -> float:
    return vol_per_period * math.sqrt(periods_per_year)


def vol_target_multiplier(forecast: float, target: float, lo: float = 0.25, hi: float = 2.0) -> float:
    """Absolute vol targeting: target / forecast, clamped to [lo, hi]."""
    if forecast <= 0:
        return 1.0
    return max(lo, min(hi, target / forecast))


def relative_sizing_multiplier(returns: list[float], lam: float = 0.85, lo: float = 0.25, hi: float = 2.0) -> float:
    """
    Unit-free sizing: long-run vol / recent (fast-EWMA) vol, clamped. >1 when the
    market is calmer than usual (size up), <1 during a vol spike (size down).
    """
    if len(returns) < 5:
        return 1.0
    recent = ewma_vol(returns, lam)
    long_run = pstdev(returns)
    if recent <= 0:
        return 1.0
    return max(lo, min(hi, long_run / recent))
