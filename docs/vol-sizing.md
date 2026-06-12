# Volatility forecasting & vol-aware sizing

Allocation (docs/capital-allocation.md) decides *how to split capital across*
strategies. Vol-aware sizing decides *how big to trade within* a symbol as its
volatility changes — smaller in turbulent regimes, larger when calm — to keep risk
per trade roughly constant.

## Forecasters (`mezna_shared.volatility`, pure Python)

- **EWMA** (RiskMetrics): σ²ₜ = λσ²ₜ₋₁ + (1−λ)r²ₜ₋₁.
- **GARCH(1,1)**: σ²ₜ = ω + α·r²ₜ₋₁ + β·σ²ₜ₋₁, fit by variance targeting
  (ω = (1−α−β)·sample var) + a small grid MLE over (α, β). Real GARCH dynamics
  without the `arch`/scipy dependency (same call as vectorbt / riskfolio).

## Analysis — `GET /backtest/volatility`

`?venue=&symbol=&interval=&days=&method=ewma|garch&target_vol=` → one-step forecast
vol (per-period + **annualised**), the GARCH params (α, β, persistence α+β), and
the **vol-target sizing multiplier** = `target_vol / forecast_vol` (clamped). Shown
on the Backtest page's Volatility panel.

## Live sizing (executor, opt-in: `VOL_TARGET_ENABLED`)

The executor uses a **unit-free relative** multiplier — `long-run vol / recent
(fast-EWMA) vol`, clamped to `[VOL_TARGET_MIN, VOL_TARGET_MAX]` — computed from
recent persisted bars for the leg's symbol. No absolute target to calibrate:

- recent vol **> usual** → multiplier **< 1** → size down
- recent vol **< usual** → multiplier **> 1** → size up

It scales the per-leg notional before quantity is computed. Best-effort: thin
history or any error → multiplier 1.0 (no scaling), so it can never break sizing.
Default OFF; enable only after reviewing its effect against the paper run.

| Var | Default | Meaning |
|---|---|---|
| `VOL_TARGET_ENABLED` | false | Master switch |
| `VOL_TARGET_INTERVAL` | 1m | Bar interval for the vol estimate |
| `VOL_TARGET_LOOKBACK_BARS` | 200 | Bars in the window |
| `VOL_TARGET_LAM` | 0.85 | Fast-EWMA decay (recent-vol estimate) |
| `VOL_TARGET_MIN` / `MAX` | 0.5 / 1.5 | Multiplier clamp |
