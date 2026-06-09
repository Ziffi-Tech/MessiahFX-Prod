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

## Phase 1 — Hardening (pre-live, the critical path)

Goal: earn the right to run the paper validation with confidence.

1. ~~**Gateway verifies the session token** (defense in depth).~~ **DONE** —
   `mezna_shared.session` verifies the HS256 token (shared `SESSION_SECRET`) +
   revocation; control actions attribute/authorise off the verified identity, the
   headers are an untrusted fallback. `GATEWAY_REQUIRE_AUTH=true` to require it.
2. ~~**Fix the test suite + add CI.**~~ **DONE** — root conftest
   `pytest_collectstart` hook resolves the shared `app` package per service, so a
   single `python -m pytest` runs the full suite (160 tests). GitHub Actions CI
   (`.github/workflows/ci.yml`): per-service backend matrix + frontend tsc/build +
   advisory ruff. `scripts/run_tests.sh` runs every suite isolated locally.
3. **Edge hardening.** Gateway rate limiting, prod-locked CORS (single origin),
   secrets out of `.env` files into Coolify secrets, dependency scanning.
4. **Reconciliation vs exchange ledger.** Compare journal positions/fills against
   the venue's reported balances/fills; alert on drift. (Freqtrade/nautilus
   patterns for precision + rate limits.)
5. **Order idempotency + state machine.** Guarantee no double-submit across
   restarts; model order lifecycle explicitly (pending→open→filled/partial/rejected).
6. **Observability that an operator watches.** Grafana dashboards (PnL, fills,
   feed health, latency SLOs), Loki log views, and alerts (feed dead, drawdown
   near limit, reconciliation drift) to Telegram/Discord.
7. **Backups + recovery drill.** Postgres backups + Redis AOF verified; restore
   tested; document RTO/RPO.

## Phase 2 — Paper validation (4+ weeks)

- Run paper with real feeds; watch the **Go-Live Readiness** gate to green.
- Transaction-cost analysis: model slippage + fees against fills; compare to
  backtest assumptions.
- Per-strategy review: Sharpe/Sortino/drawdown/win-rate — judged *good*, not just
  green. Cut or retune underperformers.

## Phase 3 — Quant depth (parallelizable with Phase 2)

- **vectorbt** walk-forward (now unblocked — persisted OHLCV exists): out-of-sample
  validation, parameter stability surfaces.
- Parameter governance: version + audit strategy params; no silent prod changes.
- Regime detector validation; `riskfolio-lib` for multi-strategy capital allocation;
  `arch`/GARCH for vol-aware sizing.
- Activate the RAG service (Qdrant reserved) for strategy-knowledge grounding.

## Phase 4 — Controlled live

- Provision exchange keys with **trade-only** scope (no withdrawal).
- Capital controls: per-strategy + global notional caps, daily loss limit auto-halt.
- Gradual rollout: one strategy, small notional → widen on evidence.
- Incident runbooks + scheduled kill-switch drills in prod.

## Phase 5 — Product polish & scale

- Live positions blotter (row-flash), journal filters/export, error boundaries,
  multi-workspace layouts, responsive/mobile, accessibility.
- Multi-account / multi-tenant if needed; per-venue feed scaling; Timescale
  retention + compression policies; horizontal scaling of stateless services.

---

## Next 5 concrete actions

1. Gateway-side token verification (Phase 1.1) — highest security leverage, small.
2. Fix `app` package collision + stand up CI (Phase 1.2) — unblocks safe iteration.
3. Exchange-ledger reconciliation + drift alert (Phase 1.4) — trust your numbers.
4. Grafana operator dashboards + feed-dead/drawdown alerts (Phase 1.6).
5. Start the paper run; drive the readiness gate to green (Phase 2).

The terminal is product-ready as an operator surface. The platform becomes
product-ready when Phase 1 is done and the paper run proves it trades cleanly for
four weeks — then, and only then, live.
