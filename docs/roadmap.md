# MeznaQuantFX — Roadmap to a Product-Ready Platform

Senior-staff view of what remains between "the terminal works end-to-end" and
"safely trading real capital as a maintainable product." Ordered by the only
principle that matters here: **capital safety before polish.**

## Where we are (honest status)

**Done and solid**
- Backend: 13 services, 6 venues (default-off), strategy engine + rotation + Kelly,
  hard risk gate + kill switch, executor adapter registry, persisted OHLCV,
  durable audit trail, opportunity funnel.
- Terminal: real-time SSE spine (ticks/risk/signals), candlestick charts, L2 depth
  ladder, START/STOP/KILL, ⌘K palette, multi-user auth + RBAC + session revocation,
  live-everywhere, data-driven go-live readiness gate.
- Deploy: Next.js terminal is the default UI; containerized; builds verified.

**Not yet product-grade**
- Gateway trusts `X-Mezna-User/Role` headers (internal-only today, but no token
  verification at the gateway = no defense in depth).
- Full test suite can't run in one pass (top-level `app` package-name collision
  across services); no CI.
- Reconciliation is internal-only (DB ground truth), not validated against the
  exchange ledger.
- No prod observability dashboards / feed-health alerting wired to the operator.
- No 4-week paper run completed; no live key provisioning / capital controls.

---

## Phase 1 — Hardening (pre-live, the critical path) — ✅ COMPLETE (1.1–1.7)

Goal: earn the right to run the paper validation with confidence. All seven items
done and tested (201-test suite + CI). The platform is now ready for the paper run.

1. ~~**Gateway verifies the session token** (defense in depth).~~ **DONE** —
   `mezna_shared.session` verifies the HS256 token (shared `SESSION_SECRET`) +
   revocation; control actions attribute/authorise off the verified identity, the
   headers are an untrusted fallback. `GATEWAY_REQUIRE_AUTH=true` to require it.
2. ~~**Fix the test suite + add CI.**~~ **DONE** — root conftest
   `pytest_collectstart` hook resolves the shared `app` package per service, so a
   single `python -m pytest` runs the full suite (160 tests). GitHub Actions CI
   (`.github/workflows/ci.yml`): per-service backend matrix + frontend tsc/build +
   advisory ruff. `scripts/run_tests.sh` runs every suite isolated locally.
3. ~~**Edge hardening.**~~ **DONE** — gateway Redis rate limiter (pure ASGI,
   SSE-safe, per-operator/IP), env-locked CORS (`CORS_ALLOWED_ORIGINS`), fail-loud
   startup on weak secrets / auth-off / open CORS in production, and
   `docs/security.md` (secrets out of images via `.containerignore` + platform
   injection). Remaining nice-to-have: dependency scanning in CI.
4. ~~**Reconciliation vs exchange ledger.**~~ **DONE** — `mezna_shared.reconciliation`
   pure drift engine (matched / mismatch / our-only / exch-only, qty + price-bps
   tolerances; 9 tests) + executor `GET /reconcile/ledger` (ccxt `fetch_positions`
   for live venues, drift → Redis + `reconciliation.drift` audit). Inert in paper
   mode. Surfaced through the gateway proxy (`executor` now mapped).
5. ~~**Order idempotency + state machine.**~~ **DONE** — deterministic
   `client_order_id` per leg (`mezna_shared.order_ids`) makes DB + exchange dedup
   effective across replays; the executor recovers a recorded result from Redis and
   reuses it instead of resubmitting. `mezna_shared.order_state` models the lifecycle
   for future limit/async orders. 14 tests.
6. ~~**Observability that an operator watches.**~~ **DONE** — feed-health +
   risk/drawdown Prometheus gauges (new exporters), an Operator Overview Grafana
   dashboard (auto-provisioned), and alert rules (service down, feed down, drawdown
   near limit, halted, high error/latency). Services also push feed-down/drawdown
   warnings to the notifications queue (Telegram/Discord) — alerting works without
   an alertmanager. See docs/observability.md.
7. ~~**Backups + recovery drill.**~~ **DONE** — `scripts/backup-postgres.sh`
   (gzip pg_dump --clean, retention), `scripts/backup-redis.sh` (BGSAVE + copy),
   `scripts/restore-postgres.sh` (confirmed, idempotent). `docs/backups.md`:
   schedule, RTO/RPO, and a quarterly restore drill. `backups/` gitignored.

## Phase 2 — Paper validation (4+ weeks) — tooling ✅, run in progress

The analytics to *judge* the run are built (the 4-week clock itself is operational):

- ~~Transaction-cost analysis~~ **DONE** — `GET /journal/pnl/tca`: realised fees
  (in bps of notional) + slippage per (strategy, venue). Compare vs backtest assumptions.
- ~~Per-strategy review~~ **DONE** — `GET /journal/pnl/by-strategy`:
  Sharpe / Sortino / max-drawdown / win-rate / profit-factor / realised P&L per strategy.
- **Performance** terminal page surfaces both + the readiness gate. See
  docs/paper-validation.md.
- **Operational (you):** run paper with real feeds; drive the readiness gate green;
  judge each strategy *good*, not just green; cut/retune laggards.

## Phase 3 — Quant depth (parallelizable with Phase 2)

- ~~Walk-forward (out-of-sample validation, parameter stability)~~ **DONE** —
  delivered engine-native (`POST /backtest/walk-forward/stat-arb`): rolling IS/OOS,
  optimise-on-IS / test-on-OOS, walk-forward-efficiency + parameter-stability +
  robust/marginal/overfit verdict, with a Backtest-page panel. vectorbt stayed
  deferred (see ADR 0001) — its heavy dep wasn't needed for the value.
- ~~Parameter governance: version + audit strategy params; no silent prod changes.~~
  **DONE** — `mezna_shared.param_governance` (canonical hash, diff, drift) + gateway
  `/api/v1/governance/strategy/*` (get / set / history / check-drift): current params
  in strategy_configs, append-only versioned history in audit_log attributed to the
  verified operator. Strategies-page governance panel. See docs/parameter-governance.md.
- ~~`riskfolio-lib` for multi-strategy capital allocation~~ **DONE** —
  `mezna_shared.allocation` (pure Python: equal / inverse-vol / risk-parity (ERC) /
  max-Sharpe tangency, with covariance + Gauss-Jordan inverse) + journal
  `GET /pnl/allocation` (date-aligned per-strategy returns → weights + capital split)
  + Allocation panel on the Performance page. riskfolio-lib stayed out (no heavy dep).
  See docs/capital-allocation.md.
- ~~`arch`/GARCH for vol-aware sizing~~ **DONE** — `mezna_shared.volatility`
  (pure-Python EWMA + GARCH(1,1) via variance-targeting grid MLE) + backtest
  `GET /volatility` + executor opt-in `VOL_TARGET_ENABLED` (relative multiplier from
  bars) + Backtest-page panel. No `arch` dep. See docs/vol-sizing.md.
- Regime detector validation.
- ~~Activate the RAG service (Qdrant) for strategy-knowledge grounding.~~ **DONE** —
  the service was fully built; wired into the platform (gateway now proxies `/rag/*`,
  the sole missing link), so RAG Studio (ingest / query / strategy profiles) works end
  to end. See docs/rag.md. Follow-up: ai-filter consults RAG context before scoring.

## Phase 4 — Controlled live

- ~~Capital controls: per-strategy + global notional caps, daily loss limit auto-halt.~~
  **DONE (code)** — risk gate now enforces `RISK_MAX_GROSS_EXPOSURE_USD`,
  `RISK_MAX_STRATEGY_EXPOSURE_USD` (open-notional caps, queried from positions at
  decision time) and `RISK_DAILY_LOSS_LIMIT_USD` (absolute daily-loss **auto-halt**),
  all default OFF. 6 tests. See docs/capital-controls.md.
- ~~Incident runbook + kill-switch drill~~ **DONE** — docs/incident-runbook.md
  (halt-first triage + a monthly kill-switch drill).
- **Operational (you):** provision exchange keys with **trade-only** scope (no
  withdrawal); gradual rollout — one strategy, small per-strategy cap → widen on
  evidence; run the kill-switch drill in prod before the first live session.

## Phase 5 — Product polish & scale

- ~~Live positions blotter (row-flash), journal filters/export, error boundaries~~
  **DONE** — positions page now shows live Current price + unrealized P&L from the
  SSE tick store with per-cell flash (FlashCell); journal already has filters + CSV
  export; route error boundaries (`error.tsx` + `global-error.tsx`) replace
  white-screens with a recoverable fallback.
- ~~Timescale retention + compression policies~~ **DONE** — migration `005` converts
  `opportunities`/`audit_log`/`market_snapshots` to hypertables (the conversion 001
  deferred) + non-destructive compression + 90d retention on `market_snapshots` only.
  Applied + verified live. `trades` kept plain (preserves `client_order_id` idempotency).
  See docs/scale.md.
- **Scope decisions (2026-06-11):** single-account (multi-tenant dropped — not
  managing separate books) and desktop-only (mobile dropped; accessibility scoped
  to keyboard nav + contrast, which the terminal largely has via the ⌘K palette).
- ~~Full stack online~~ **DONE** — RAG (qdrant + rag healthy, verified through the
  gateway proxy) and observability (prometheus + grafana on :3002 + loki + promtail)
  now run alongside the trading stack; 18 containers green on the 6 GiB machine
  (~1.5 GiB steady-state). Fixed en route: qdrant healthcheck (no curl in image →
  bash /dev/tcp probe), loki healthcheck (distroless, no probe possible → dependents
  wait on service_started), grafana host port 3000→3002 (terminal owns 3000),
  backtest healthcheck (no curl in image → python urllib probe), and the dev
  terminal container's pnpm-9-vs-10 `pnpm-workspace.yaml` crash-loop.
- Remaining (in priority order): multi-workspace layouts; keyboard-nav/contrast
  pass; per-venue feed scaling; horizontal scaling of stateless services;
  continuous aggregates for dashboard rollups.

---

## Next actions (Phase 1 done)

1. **Deploy with prod config** — `ENVIRONMENT=production`, strong `SESSION_SECRET`
   (gateway + dashboard), `GATEWAY_REQUIRE_AUTH=true`, locked `CORS_ALLOWED_ORIGINS`,
   schedule the backups. Rebuild images (new shared/gateway modules).
2. **Start the paper run** — drive the Go-Live Readiness gate to green; watch the
   Operator dashboard + alerts.
3. **Phase 3 in parallel** — vectorbt walk-forward (now unblocked), TCA/slippage,
   parameter governance.

The terminal is product-ready as an operator surface and Phase 1 hardening is
complete. The platform becomes product-ready when the paper run proves it trades
cleanly for four weeks — then, and only then, live.
