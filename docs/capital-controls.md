# Capital controls (Phase 4 — controlled live)

Hard, operator-set limits that bound live risk. They sit in the risk gate
([checker.py](../services/risk/app/checker.py)), so **no order bypasses them** —
the gate is the single pre-trade authority. All default **OFF** (0) so paper
behaviour is unchanged; set them before going live, start small, widen on evidence.

## The gate (priority order)

1. kill switch · 2. strategy enabled · 3. cooldown · 4. daily drawdown % → **auto-halt**
· **4b. daily loss USD → auto-halt** · 5. max open positions · **5b. exposure caps**
· 6. consecutive losses → cooldown · 7. positive edge. First failure rejects.

## New controls

| Limit | Env | Effect |
|---|---|---|
| Global notional cap | `RISK_MAX_GROSS_EXPOSURE_USD` | Reject if gross open notional + this order > cap |
| Per-strategy notional cap | `RISK_MAX_STRATEGY_EXPOSURE_USD` | Same, per strategy — supports gradual rollout |
| Absolute daily-loss halt | `RISK_DAILY_LOSS_LIMIT_USD` | Daily realised loss ≥ limit → reject **+ auto-halt** |

- **Exposure** is the live OPEN notional (Σ|net_qty·avg_price|) from the positions
  table, queried at decision time — only when a cap is set (no overhead when off).
- The new order's notional is estimated as the configured per-trade size
  (`PAPER_CAPITAL_USD × RISK_MAX_PER_TRADE_PCT`); caps are conservative.
- **Auto-halt** sets `risk:halt` (stops the whole system, defense in depth) and
  pushes a notification — same path as the drawdown-% halt.

## Gradual rollout (the Phase-4 pattern)

1. One strategy, small `RISK_MAX_STRATEGY_EXPOSURE_USD`, tight `RISK_DAILY_LOSS_LIMIT_USD`.
2. Watch the Operator dashboard + Performance/TCA + reconciliation for a week.
3. Widen the per-strategy cap and add the next strategy only on clean evidence.
4. `RISK_MAX_GROSS_EXPOSURE_USD` bounds the whole book throughout.

The caps are a floor on safety, not a substitute for judgement — combine with the
go-live checklist (docs/go-live-checklist.md) and the incident runbook
(docs/incident-runbook.md).
