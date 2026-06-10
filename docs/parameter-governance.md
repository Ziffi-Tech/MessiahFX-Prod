# Parameter governance

Strategy parameters decide behaviour, yet changing them was a silent overwrite.
Governance makes every change **versioned, attributed, and drift-checkable** — so
the params running live can always be traced to (and compared against) the set a
backtest validated.

## Model

- **Current params** live in `strategy_configs.params` (one row per strategy).
- **History** is append-only in `audit_log` (`event_type=strategy.params_changed`):
  each change records the new params, a canonical **hash**, a monotonic **version**,
  a structured **diff** vs the previous set, the **source** (manual / optimize /
  walk_forward / backtest), a **reason**, and the **verified operator** who made it.
- `mezna_shared.param_governance` provides the pure primitives: `param_hash`
  (order-independent), `diff_params`, `drift_report`.

## API (gateway, `/api/v1/governance`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/strategy/{type}` | Current params + hash + version + who/when |
| PUT | `/strategy/{type}` | Set params — records a versioned, audited change |
| GET | `/strategy/{type}/history` | Change history (newest first) |
| POST | `/strategy/{type}/check-drift` | Compare a reference set (e.g. backtested) vs live |

`PUT` requires a verified session (Phase 1.1); viewers are blocked at the proxy.

## Workflow

1. Backtest / optimise / walk-forward a strategy → you have a validated param set.
2. Before deploying, **check drift**: `POST …/check-drift` with the backtested
   set → see exactly how live differs.
3. Deploy via `PUT …/strategy/{type}` with `source=walk_forward` + a reason. The
   change is versioned and audited; the Strategies-page **Parameter Governance**
   panel shows the new version, hash, and the diff in the history.

No silent drift: if live params ever diverge from what was validated, the drift
check and the audit trail make it visible.

## Next

Have the strategy service read its parameters from the governed store (with env
fallback) so the governed set is authoritative, not just recorded.
