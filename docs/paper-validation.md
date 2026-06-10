# Paper validation (Phase 2)

Before live, the system must prove it trades cleanly for **4+ weeks** on paper.
The terminal's **Performance** page is the cockpit for that judgement; this is how
to read it.

## The loop

1. Configure feeds + enable strategies in paper mode; start the bot.
2. Watch daily: the **Go-Live Readiness** gate (Risk + Performance pages), the
   **Operator** Grafana dashboard, and alerts.
3. Weekly: review per-strategy performance + transaction costs (below).
4. At 4 weeks with the gate green and the numbers *good*, run the manual
   go-live sign-off (docs/go-live-checklist.md).

## Per-strategy review — `GET /journal/pnl/by-strategy`

For each strategy over the window: win rate, profit factor, **Sharpe**, **Sortino**,
**max drawdown**, fees, realised P&L. Judge each strategy on its own merits:

- **Sharpe ≥ 1, Sortino ≥ 1** — risk-adjusted return is real, downside controlled.
- **Profit factor > 1** with enough trades to be meaningful (not 2 lucky wins).
- **Max drawdown** within tolerance for the strategy's style.

Cut or retune anything that's merely break-even or relies on a handful of outliers.
"Green" (positive P&L) is necessary, not sufficient.

## Transaction-cost analysis — `GET /journal/pnl/tca`

Realised **fee bps** (fees ÷ notional) and **avg slippage bps** per (strategy, venue).
Compare against the assumptions baked into the backtest (`*_TAKER_FEE_BPS`,
spread estimates):

- If realised fee/slippage bps materially exceed the backtest's, the backtested
  edge is overstated — re-run backtests with realistic costs before trusting it.
- Watch per-venue: a cheap edge on one venue can be eaten by costs on another.

## Why this matters

A strategy that's green in paper but whose edge is smaller than its real
transaction costs will lose money live. Phase 2 exists to catch exactly that —
prove the edge survives real fills, not just backtest assumptions.
