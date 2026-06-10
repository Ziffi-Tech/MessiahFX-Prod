# Multi-strategy capital allocation

Phase 2 answers *which strategies are good*; this answers *how much capital each
should get*. The allocator turns each strategy's realised-P&L history into capital
weights.

## Methods — `GET /journal/pnl/allocation?days=&method=&capital=`

| Method | Idea | When |
|---|---|---|
| `equal_weight` | 1/N | baseline / cold start |
| `inverse_vol` | weight ∝ 1/σ | simple, robust, down-weights the volatile |
| `risk_parity` | each strategy contributes equal portfolio risk (ERC) | default — diversified by risk, not dollars |
| `max_sharpe` | tangency portfolio Σ⁻¹μ, long-only | when you trust the estimated means |

Input is the **date-aligned daily realised-P&L series** per strategy (non-trading
days filled with 0). Allocation runs over strategies with usable history (≥2 days,
non-zero variance); the rest get weight 0. Output: weights, the capital split, and
each strategy's daily vol + **risk contribution**.

## No heavy dependency

Built in pure Python (`mezna_shared.allocation`) — covariance + a Gauss-Jordan
matrix inverse — because the strategy count is tiny and `mezna_shared` is baked
into every service image. `riskfolio-lib` was deliberately **not** added (same
call as vectorbt in ADR 0001); it remains an option only if advanced methods
(HRP, CVaR) are ever wanted.

## Terminal

The **Performance** page Capital Allocation panel: method selector + total-capital
input, a weight bar, and a per-strategy table (weight, capital, daily vol, risk
contribution). Risk parity is the default — it equalises each strategy's risk
contribution rather than its dollar weight.

## Caveat

Allocation is descriptive, from realised P&L. It is an input to sizing decisions,
not an auto-rebalance — review against the per-strategy performance + TCA before
shifting capital.
