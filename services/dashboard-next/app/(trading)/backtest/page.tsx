"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Play, BarChart3, Zap, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { BacktestResult, MonteCarloResult, GridSearchEntry } from "@/types/api";
import { WalkForwardPanel } from "@/components/trading/walk-forward-panel";

type Tab = "backtest" | "monte_carlo" | "optimise";

// ── Shared input styles ───────────────────────────────────────────────────────
const inputStyle: React.CSSProperties = {
  background: "var(--bg-surface-2)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
};

function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className="text-xs" style={{ color: "var(--text-secondary)" }}>
      {children}
    </label>
  );
}

function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function Select({ value, onChange, options }: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full px-3 py-2 text-sm rounded outline-none"
      style={inputStyle}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  );
}

function NumInput({ value, onChange, min, max, step }: {
  value: number; onChange: (v: number) => void;
  min?: number; max?: number; step?: number;
}) {
  return (
    <input
      type="number"
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      min={min} max={max} step={step}
      className="w-full px-3 py-2 text-sm rounded outline-none"
      style={inputStyle}
    />
  );
}

// ── Equity curve mini-chart ────────────────────────────────────────────────────
function EquityCurve({ curve, capital }: { curve: BacktestResult["equity_curve"]; capital: number }) {
  if (!curve.length) return null;
  const vals = curve.map((p) => p.equity_usd);
  const min  = Math.min(...vals);
  const max  = Math.max(...vals);
  const span = max - min || 1;
  return (
    <div className="panel p-5">
      <p className="text-xs font-semibold mb-4" style={{ color: "var(--text-primary)" }}>
        Equity Curve
      </p>
      <div className="h-40 flex items-end gap-px">
        {curve.map((pt, i) => {
          const pct = ((pt.equity_usd - min) / span) * 100;
          return (
            <div
              key={i}
              className="flex-1 min-w-px rounded-sm"
              style={{
                height: `${Math.max(pct, 2)}%`,
                background:
                  pt.equity_usd >= capital ? "var(--green)" : "var(--red)",
                opacity: 0.8,
              }}
              title={`$${pt.equity_usd.toFixed(2)}`}
            />
          );
        })}
      </div>
      <div className="flex justify-between text-[10px] mt-2" style={{ color: "var(--text-tertiary)" }}>
        <span>Start</span>
        <span>End · ${vals[vals.length - 1]?.toFixed(2)}</span>
      </div>
    </div>
  );
}

// ── Monte Carlo result display ────────────────────────────────────────────────
function MonteCarloDisplay({ mc, interp }: {
  mc: MonteCarloResult;
  interp: Record<string, string>;
}) {
  const pnlColour = (v: number) => (v >= mc.capital_usd ? "var(--green)" : "var(--red)");
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: "P10 Equity",  v: `$${mc.equity_p10.toLocaleString("en-US", { maximumFractionDigits: 0 })}`, c: pnlColour(mc.equity_p10) },
          { label: "P50 Equity",  v: `$${mc.equity_p50.toLocaleString("en-US", { maximumFractionDigits: 0 })}`, c: pnlColour(mc.equity_p50) },
          { label: "P90 Equity",  v: `$${mc.equity_p90.toLocaleString("en-US", { maximumFractionDigits: 0 })}`, c: pnlColour(mc.equity_p90) },
          { label: "Max DD P90",  v: `${mc.max_dd_p90.toFixed(1)}%`, c: mc.max_dd_p90 > 20 ? "var(--red)" : mc.max_dd_p90 > 10 ? "var(--orange)" : "var(--green)" },
          { label: "Ruin Risk 25%", v: `${(mc.ruin_prob_25pct * 100).toFixed(1)}%`, c: mc.ruin_prob_25pct > 0.1 ? "var(--red)" : "var(--green)" },
          { label: "Ruin Risk 50%", v: `${(mc.ruin_prob_50pct * 100).toFixed(1)}%`, c: mc.ruin_prob_50pct > 0.05 ? "var(--red)" : "var(--green)" },
          { label: "Kelly Fraction", v: `${(mc.kelly_fraction * 100).toFixed(2)}%`, c: "var(--blue)" },
          { label: "Simulations",   v: mc.n_simulations.toLocaleString(), c: "var(--text-secondary)" },
        ].map((s) => (
          <div key={s.label} className="panel p-4 space-y-1">
            <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{s.label}</p>
            <p className="mono text-base font-bold" style={{ color: s.c }}>{s.v}</p>
          </div>
        ))}
      </div>
      {/* Interpretation */}
      <div className="panel p-4 space-y-2">
        <p className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Interpretation</p>
        {Object.values(interp).map((line, i) => (
          <p key={i} className="text-xs" style={{ color: "var(--text-secondary)" }}>· {line}</p>
        ))}
      </div>
    </div>
  );
}

// ── Grid search result table ──────────────────────────────────────────────────
function GridSearchTable({ results }: { results: GridSearchEntry[] }) {
  return (
    <div className="panel overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)" }}>
              {["Rank", "Params", "Sharpe", "Net P&L", "Win Rate", "Max DD", "Kelly %"].map((h) => (
                <th key={h} className="px-4 py-2 text-left font-medium" style={{ color: "var(--text-tertiary)" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {results.slice(0, 15).map((r, i) => (
              <tr
                key={i}
                className="transition-colors hover:bg-[var(--bg-hover)]"
                style={{ borderBottom: "1px solid var(--border-subtle)" }}
              >
                <td className="px-4 py-2 mono" style={{ color: i === 0 ? "var(--yellow)" : "var(--text-tertiary)" }}>
                  {i === 0 ? "★" : `#${i + 1}`}
                </td>
                <td className="px-4 py-2">
                  <div className="flex gap-1 flex-wrap">
                    {Object.entries(r.params).map(([k, v]) => (
                      <span key={k} className="badge badge-gray mono">{k.replace(/_/g, " ")}={v}</span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-2 mono font-bold" style={{ color: r.sharpe_ratio >= 1 ? "var(--green)" : r.sharpe_ratio >= 0 ? "var(--orange)" : "var(--red)" }}>
                  {r.sharpe_ratio.toFixed(2)}
                </td>
                <td className="px-4 py-2 mono" style={{ color: r.net_pnl_usd >= 0 ? "var(--green)" : "var(--red)" }}>
                  {r.net_pnl_usd >= 0 ? "+" : ""}${r.net_pnl_usd.toFixed(0)}
                </td>
                <td className="px-4 py-2 mono">{(r.win_rate * 100).toFixed(1)}%</td>
                <td className="px-4 py-2 mono" style={{ color: r.max_drawdown_pct > 20 ? "var(--red)" : "var(--text-secondary)" }}>
                  {r.max_drawdown_pct.toFixed(1)}%
                </td>
                <td className="px-4 py-2 mono" style={{ color: "var(--blue)" }}>
                  {(r.kelly_fraction * 100).toFixed(2)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="px-4 py-2 text-[10px]" style={{ color: "var(--text-tertiary)", borderTop: "1px solid var(--border-subtle)" }}>
        Showing top 15 of {results.length} combinations · Sorted by Sharpe ratio
      </div>
    </div>
  );
}

// ── Metric cards for backtest result ─────────────────────────────────────────
function BacktestMetrics({ r }: { r: BacktestResult }) {
  const items = [
    { label: "Total Return",  v: `${r.total_return_pct >= 0 ? "+" : ""}${r.total_return_pct.toFixed(2)}%`,    c: r.total_return_pct >= 0 },
    { label: "Net P&L",       v: `$${r.net_pnl_usd.toFixed(2)}`,                                               c: r.net_pnl_usd >= 0 },
    { label: "Win Rate",      v: `${(r.win_rate * 100).toFixed(1)}%`,                                           neutral: true },
    { label: "Sharpe",        v: r.sharpe_ratio.toFixed(2),                                                     c: r.sharpe_ratio >= 1 },
    { label: "Max Drawdown",  v: `${r.max_drawdown_pct.toFixed(2)}%`,                                           c: false },
    { label: "Total Trades",  v: String(r.total_trades),                                                         neutral: true },
    { label: "Total Fees",    v: `$${r.total_fees_usd.toFixed(2)}`,                                             c: false },
    { label: "Avg Hold",      v: `${r.avg_hold_candles.toFixed(1)} bars`,                                        neutral: true },
  ] as const;
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {items.map((s) => (
        <div key={s.label} className="panel p-4 space-y-1">
          <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{s.label}</p>
          <p
            className="mono text-base font-bold"
            style={{
              color: "neutral" in s && s.neutral
                ? "var(--text-primary)"
                : "c" in s && s.c
                ? "var(--green)"
                : "var(--red)",
            }}
          >
            {s.v}
          </p>
        </div>
      ))}
    </div>
  );
}


// ── Main page ─────────────────────────────────────────────────────────────────
export default function BacktestPage() {
  const [tab, setTab] = useState<Tab>("backtest");

  // Backtest params
  const [btStrategy, setBtStrategy] = useState<"funding_arb" | "stat_arb">("funding_arb");
  const [btDays, setBtDays] = useState(90);
  const [btCapital, setBtCapital] = useState(5000);
  const [btMinEdge, setBtMinEdge] = useState(5);
  const [btFee, setBtFee] = useState(7.5);
  const [btWindow, setBtWindow] = useState(100);
  const [btEntryZ, setBtEntryZ] = useState(2.0);

  // Monte Carlo params
  const [mcStrategy, setMcStrategy] = useState<"funding_arb" | "stat_arb">("funding_arb");
  const [mcDays, setMcDays] = useState(90);
  const [mcCapital, setMcCapital] = useState(5000);
  const [mcSimulations, setMcSimulations] = useState(1000);

  // Optimiser params
  const [optStrategy, setOptStrategy] = useState<"funding_arb" | "stat_arb">("funding_arb");
  const [optDays, setOptDays] = useState(120);
  const [optCapital, setOptCapital] = useState(5000);

  // Backtest mutation
  const btMut = useMutation({
    mutationFn: () =>
      btStrategy === "funding_arb"
        ? api.backtest.fundingArb({ days: btDays, capital_usd: btCapital, min_edge_bps: btMinEdge, fee_bps: btFee })
        : api.backtest.statArb({ days: btDays, capital_usd: btCapital, window: btWindow, entry_z: btEntryZ }),
  });

  // Monte Carlo mutation
  const mcMut = useMutation({
    mutationFn: () =>
      mcStrategy === "funding_arb"
        ? api.backtest.monteCarloFundingArb({ days: mcDays, capital_usd: mcCapital, n_simulations: mcSimulations })
        : api.backtest.monteCarloStatArb({ days: mcDays, capital_usd: mcCapital }),
  });

  // Optimiser mutation
  const optMut = useMutation({
    mutationFn: () =>
      optStrategy === "funding_arb"
        ? api.backtest.optimizeFundingArb({ days: optDays, capital_usd: optCapital })
        : api.backtest.optimizeStatArb({ days: optDays, capital_usd: optCapital }),
  });

  const strategyOptions = [
    { value: "funding_arb", label: "Funding Arbitrage" },
    { value: "stat_arb",   label: "Statistical Arbitrage" },
  ];

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "backtest",     label: "Backtest",     icon: <BarChart3 size={13} /> },
    { id: "monte_carlo",  label: "Monte Carlo",  icon: <Zap size={13} /> },
    { id: "optimise",     label: "Optimiser",    icon: <Search size={13} /> },
  ];

  return (
    <div className="space-y-5">
      {/* Tab bar */}
      <div className="flex gap-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className="flex items-center gap-2 px-4 py-2 rounded text-xs font-medium transition-colors"
            style={{
              background: tab === t.id ? "var(--blue-dim)" : "transparent",
              color: tab === t.id ? "var(--blue)" : "var(--text-secondary)",
              border: `1px solid ${tab === t.id ? "rgba(14,165,233,0.3)" : "var(--border)"}`,
            }}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Backtest tab ──────────────────────────────────────────────────── */}
      {tab === "backtest" && (
        <div className="space-y-4">
          <div className="panel p-5 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <FieldGroup label="Strategy">
                <Select value={btStrategy} onChange={(v) => setBtStrategy(v as typeof btStrategy)} options={strategyOptions} />
              </FieldGroup>
              <FieldGroup label="Lookback (days)">
                <NumInput value={btDays} onChange={setBtDays} min={7} max={365} />
              </FieldGroup>
              <FieldGroup label="Capital (USD)">
                <NumInput value={btCapital} onChange={setBtCapital} min={100} step={100} />
              </FieldGroup>

              {btStrategy === "funding_arb" && (
                <>
                  <FieldGroup label="Min Edge (bps)">
                    <NumInput value={btMinEdge} onChange={setBtMinEdge} min={1} max={50} step={0.5} />
                  </FieldGroup>
                  <FieldGroup label="Fee (bps)">
                    <NumInput value={btFee} onChange={setBtFee} min={1} max={30} step={0.5} />
                  </FieldGroup>
                </>
              )}

              {btStrategy === "stat_arb" && (
                <>
                  <FieldGroup label="Z-Score Window">
                    <NumInput value={btWindow} onChange={setBtWindow} min={20} max={500} />
                  </FieldGroup>
                  <FieldGroup label="Entry Z-Score">
                    <NumInput value={btEntryZ} onChange={setBtEntryZ} min={0.5} max={5} step={0.1} />
                  </FieldGroup>
                </>
              )}
            </div>

            <button
              onClick={() => btMut.mutate()}
              disabled={btMut.isPending}
              className="flex items-center gap-2 px-5 py-2 rounded text-sm font-semibold disabled:opacity-50 transition-opacity"
              style={{ background: "var(--blue)", color: "#fff" }}
            >
              <Play size={14} />
              {btMut.isPending ? "Running…" : "Run Backtest"}
            </button>

            {btMut.isError && (
              <p className="text-xs" style={{ color: "var(--red)" }}>
                {(btMut.error as Error)?.message ?? "Service unavailable"}
              </p>
            )}
          </div>

          {btMut.data && (
            <div className="space-y-4">
              <BacktestMetrics r={btMut.data} />
              <EquityCurve curve={btMut.data.equity_curve} capital={btCapital} />
            </div>
          )}
        </div>
      )}

      {/* ── Monte Carlo tab ───────────────────────────────────────────────── */}
      {tab === "monte_carlo" && (
        <div className="space-y-4">
          <div
            className="panel p-4 text-xs"
            style={{ background: "var(--purple-dim)", borderColor: "rgba(167,139,250,0.3)" }}
          >
            <p style={{ color: "var(--text-secondary)" }}>
              Bootstrap-resamples the trade log across N simulated paths. Shows the distribution of outcomes —
              not just the average. Use P10 equity as your realistic downside; P50 as expected; P90 as upside.
            </p>
          </div>
          <div className="panel p-5 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <FieldGroup label="Strategy">
                <Select value={mcStrategy} onChange={(v) => setMcStrategy(v as typeof mcStrategy)} options={strategyOptions} />
              </FieldGroup>
              <FieldGroup label="Lookback (days)">
                <NumInput value={mcDays} onChange={setMcDays} min={30} max={365} />
              </FieldGroup>
              <FieldGroup label="Capital (USD)">
                <NumInput value={mcCapital} onChange={setMcCapital} min={100} step={100} />
              </FieldGroup>
              <FieldGroup label="Simulations">
                <NumInput value={mcSimulations} onChange={setMcSimulations} min={100} max={10000} step={100} />
              </FieldGroup>
            </div>
            <button
              onClick={() => mcMut.mutate()}
              disabled={mcMut.isPending}
              className="flex items-center gap-2 px-5 py-2 rounded text-sm font-semibold disabled:opacity-50"
              style={{ background: "var(--purple)", color: "#fff" }}
            >
              <Zap size={14} />
              {mcMut.isPending ? `Simulating ${mcSimulations} paths…` : "Run Monte Carlo"}
            </button>
          </div>

          {mcMut.data && (
            <MonteCarloDisplay mc={mcMut.data.monte_carlo} interp={mcMut.data.interpretation} />
          )}
        </div>
      )}

      {/* ── Optimiser tab ─────────────────────────────────────────────────── */}
      {tab === "optimise" && (
        <div className="space-y-4">
          <div className="panel p-4 text-xs" style={{ background: "var(--orange-dim)", borderColor: "rgba(251,146,60,0.3)" }}>
            <p style={{ color: "var(--text-secondary)" }}>
              Grid search over parameter combinations. Data is downloaded <strong style={{ color: "var(--text-primary)" }}>once</strong> then
              all combinations run in-memory. Results sorted by Sharpe ratio.
              Always validate best params on a held-out period before deploying to live.
            </p>
          </div>
          <div className="panel p-5 space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <FieldGroup label="Strategy">
                <Select value={optStrategy} onChange={(v) => setOptStrategy(v as typeof optStrategy)} options={strategyOptions} />
              </FieldGroup>
              <FieldGroup label="Lookback (days)">
                <NumInput value={optDays} onChange={setOptDays} min={30} max={365} />
              </FieldGroup>
              <FieldGroup label="Capital (USD)">
                <NumInput value={optCapital} onChange={setOptCapital} min={100} step={100} />
              </FieldGroup>
            </div>
            <button
              onClick={() => optMut.mutate()}
              disabled={optMut.isPending}
              className="flex items-center gap-2 px-5 py-2 rounded text-sm font-semibold disabled:opacity-50"
              style={{ background: "var(--orange)", color: "#fff" }}
            >
              <Search size={14} />
              {optMut.isPending ? "Running grid search…" : "Run Grid Search"}
            </button>
          </div>

          {optMut.data && (
            <div className="space-y-3">
              {optMut.data.best_params && (
                <div className="panel p-4 flex items-center gap-3">
                  <span className="text-lg">★</span>
                  <div>
                    <p className="text-xs font-semibold" style={{ color: "var(--yellow)" }}>
                      Best params (Sharpe {optMut.data.ranked_results[0]?.sharpe_ratio.toFixed(2)})
                    </p>
                    <div className="flex gap-2 mt-1">
                      {Object.entries(optMut.data.best_params).map(([k, v]) => (
                        <span key={k} className="badge badge-gray mono">{k}={v}</span>
                      ))}
                    </div>
                  </div>
                </div>
              )}
              <GridSearchTable results={optMut.data.ranked_results} />
            </div>
          )}
        </div>
      )}

      {/* Walk-forward out-of-sample validation */}
      <WalkForwardPanel />
    </div>
  );
}
