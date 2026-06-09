# Go-Live Checklist — paper → live

Live trading stays gated until the paper-validation run passes. The terminal
evaluates the objective part of this gate automatically; the rest is a human
sign-off. **No real capital is committed until every item below is green.**

## Automated gate — `GET /journal/readiness`

Surfaced in the terminal on the **Risk** page (Go-Live Readiness panel), refreshed
every 60s. Override thresholds with `?min_paper_days=&min_trades=`.

Critical criteria (all must pass for `ready: true`):

| Criterion | Default | Meaning |
|---|---|---|
| `paper_duration` | ≥ 28 days | Days since the first paper fill / bot start |
| `kill_switch_tested` | ≥ 1 | Kill switch exercised at least once (audit log) |
| `sufficient_trades` | ≥ 50 | Filled trades — enough for statistical meaning |
| `round_trips_closed` | ≥ 1 | Positions have closed (realized P&L populated) |
| `still_paper` | 0 live fills | No accidental live trading occurred |

Advisory (surfaced, non-blocking): `risk_breaches` — count of drawdown/limit
breach events to review before flipping live.

## Manual sign-off (not automatable)

- [ ] Kill switch tested **in the production environment** (not just locally), and
      the halt propagated to the executor within seconds.
- [ ] Reconciliation reviewed: `GET /executor/reconcile/ledger` reports `ok: true`
      (our positions match the exchange ledger) under live keys.
- [ ] Risk limits reviewed and set deliberately (`RISK_MAX_*`, drawdown, per-trade).
- [ ] Real exchange API keys provisioned with **trade-only** scope (no withdrawal).
- [ ] Per-strategy paper performance reviewed (Sharpe, drawdown, win rate) and
      judged acceptable — not just "green", but *good*.
- [ ] `SESSION_SECRET` set to a strong value; `DASHBOARD_USERS` roster reviewed.
- [ ] Alerting verified (Telegram/Discord) end to end.
- [ ] Capital at risk for the first live week is intentionally small.

## Flipping to live

Only after the above:

1. Set `TRADING_MODE=live` and each strategy's `paper_mode=false` deliberately.
2. Start with one strategy and a small notional; widen gradually.
3. Watch the terminal + alerts closely for the first sessions; keep KILL one click away.

The automated gate is necessary, not sufficient — it proves the system *ran*
cleanly for long enough; the manual review proves it ran *well*.
