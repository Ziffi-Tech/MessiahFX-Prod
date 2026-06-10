# Incident runbook + kill-switch drill

For live trading. Keep this short and act first — **halt, then diagnose.**

## Stop trading NOW

- **Terminal:** topbar **KILL** (or ⌘K → "KILL"). Sets `risk:halt`; the executor
  stops processing within seconds. Open positions are **not** auto-closed.
- **If the terminal is down:** `podman exec mezna-redis redis-cli SET risk:halt 1`
- Resume only deliberately: topbar **Start** (or `redis-cli SET risk:halt 0`) after
  you understand and have fixed the cause.

## Triage (in order)

1. **Halt** (above).
2. **Open exposure** — Positions page / `GET /executor/reconcile/ledger`: do our
   books match the exchange? Flatten manually on the venue if needed.
3. **Why** — Operator Grafana dashboard + alerts; `audit_log` (kill/halt/params);
   service logs (Loki). Common: feed down (stale prices), drawdown/loss auto-halt,
   reconciliation drift, a bad param change (governance history shows the diff).
4. **Contain** — disable the offending strategy (Strategies page); tighten caps
   (`RISK_MAX_*_EXPOSURE_USD`, `RISK_DAILY_LOSS_LIMIT_USD`).
5. **Recover** — fix root cause; if state is suspect, restore from backup
   (docs/backups.md). Resume in paper or with a tiny cap first.
6. **Write it up** — what happened, blast radius, fix, prevention.

## Auto-halt triggers (already wired)

The risk gate auto-halts on: daily **drawdown %** ≥ limit, daily **loss USD** ≥
limit. Feed-down and drawdown-near-limit push notifications (Telegram/Discord).

## Kill-switch drill (run on a schedule — monthly in prod)

An untested kill switch is not a safety control.

1. In paper (or a quiet window), hit **KILL**; confirm `risk:halt=1`, an audit
   `kill_switch.activated` record, and that the executor logs the halt re-check.
2. Submit/await a signal — confirm it is **dropped**, not executed.
3. **Reset** (Start / confirm reset); confirm trading resumes and the reset is audited.
4. Time it end to end → that's your real halt latency. File anything surprising.

Do the same drill in the **production** environment before the first live session,
and after any change to the risk path.
