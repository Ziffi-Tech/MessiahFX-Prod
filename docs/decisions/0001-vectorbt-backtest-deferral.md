# ADR 0001 — Defer vectorbt for the backtest engine

- **Status:** Deferred (re-assessed 2026-06-08)
- **Service:** `services/backtest`
- **Supersedes:** the informal "vectorbt deferred — numpy-2/numba compat" note

## Context

The backtest service ships a purpose-built, event-driven engine
([engine.py](../../services/backtest/app/engine.py)) covering:

- `run_funding_arb` and `run_stat_arb` simulations (close-fill, taker fees, no
  look-ahead),
- standard risk metrics — Sharpe / Sortino / Calmar via `empyrical`, plus a
  hand-rolled Sharpe fallback,
- bootstrap **Monte Carlo** over the trade log (equity/drawdown percentiles,
  ruin probability, Kelly sizing),
- **grid search** over strategy parameters.

`vectorbt` was previously parked as "blocked by numpy-2 / numba
incompatibility." This ADR records a fresh assessment.

## Finding: the compatibility blocker is resolved

As of **numba 0.65.1** (declares `numpy>=1.22,<2.5`) and **vectorbt 1.0.0**, a
`pip install --dry-run vectorbt` against the platform stack (Python 3.13,
`numpy>=2.0`, locally 2.4.6) resolves **without downgrading numpy** — numpy
2.4.6 satisfies numba's range. The original reason for deferral no longer holds.

## Decision: stay deferred anyway — but for sharper reasons

We do **not** adopt vectorbt now. The real blockers are no longer about numpy:

1. **No persisted historical OHLCV.** vectorbt backtests price/signal arrays
   over real history. The platform only keeps a ~500-tick live Redis cache,
   resampled to bars in-memory for live detection
   ([bars.py](../../shared/mezna_shared/bars.py)). There is no historical bar
   store to backtest against. **This is the true prerequisite.**
2. **Heavy dependency footprint.** vectorbt pulls ~45 packages (numba, llvmlite,
   matplotlib, plotly, ipython, ipywidgets, scikit-learn, dateparser, …) into a
   service that is currently lean. Significant image-size and cold-start cost.
3. **Paradigm mismatch.** vectorbt's `Portfolio.from_signals/from_orders` model
   is signal-array based. The current strategies don't map cleanly: funding-arb
   P&L is *funding income*, not a price-series return, and stat-arb is a
   z-score spread reversion. Porting them is a research task, not a drop-in.
4. **No missing capability today.** The institutional metrics vectorbt is prized
   for (Sharpe/Sortino/Calmar, Monte Carlo, parameter sweeps) already exist in
   the hand-rolled engine. vectorbt would add *speed and breadth*, not coverage.

The ceiling constraint is also worth noting: numba caps `numpy<2.5`. If the
platform bumps to numpy ≥ 2.5 before numba catches up, adopting vectorbt would
then force a numpy split across services.

## Revisit when

- A **persisted OHLCV/bar history** exists (the directional bar-based strategies
  — breakout / momentum / mean-reversion — are the natural first candidates for
  a vectorized portfolio backtest), **and**
- we want fast, broad parameter sweeps that the event-driven engine makes slow,
  **and**
- numba still supports the platform's numpy (re-check the `<2.5` ceiling).

Until then the existing engine is the supported backtest path.
