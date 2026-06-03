"use client";

import { useState } from "react";
import type { BacktestResult } from "@/types/api";
import { BarChart3, Play, TrendingUp, TrendingDown } from "lucide-react";

const STRATEGIES = ["funding_arb", "stat_arb", "swing"];

export default function BacktestPage() {
  const [strategy, setStrategy] = useState("funding_arb");
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState(new Date().toISOString().slice(0, 10));
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const run = async () => {
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await fetch("/api/gateway/backtest/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ strategy_type: strategy, start_date: startDate, end_date: endDate }),
      });
      if (res.ok) setResult(await res.json());
      else setError(await res.text());
    } catch (e) {
      setError("Backtest service unavailable");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <h1 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>Backtest</h1>

      {/* Config panel */}
      <div className="panel p-5 space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="space-y-1.5">
            <label className="text-xs" style={{ color: "var(--text-secondary)" }}>Strategy</label>
            <select
              value={strategy}
              onChange={e => setStrategy(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded outline-none"
              style={{ background: "var(--bg-surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            >
              {STRATEGIES.map(s => (
                <option key={s} value={s}>{s.replace("_", " ").toUpperCase()}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs" style={{ color: "var(--text-secondary)" }}>Start Date</label>
            <input
              type="date"
              value={startDate}
              onChange={e => setStartDate(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded outline-none"
              style={{ background: "var(--bg-surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs" style={{ color: "var(--text-secondary)" }}>End Date</label>
            <input
              type="date"
              value={endDate}
              onChange={e => setEndDate(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded outline-none"
              style={{ background: "var(--bg-surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
          </div>
        </div>
        <button
          onClick={run}
          disabled={loading}
          className="flex items-center gap-2 px-5 py-2 rounded text-sm font-semibold disabled:opacity-50 transition-opacity"
          style={{ background: "var(--blue)", color: "#fff" }}
        >
          <Play size={14} />
          {loading ? "Running backtest…" : "Run Backtest"}
        </button>
        {error && <p className="text-xs" style={{ color: "var(--red)" }}>{error}</p>}
      </div>

      {/* Results */}
      {result && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[
              { label: "Total Return", value: `${result.total_return_pct >= 0 ? "+" : ""}${result.total_return_pct.toFixed(2)}%`, positive: result.total_return_pct >= 0 },
              { label: "Win Rate", value: `${(result.win_rate * 100).toFixed(1)}%`, neutral: true },
              { label: "Sharpe Ratio", value: result.sharpe_ratio?.toFixed(2) ?? "—", positive: (result.sharpe_ratio ?? 0) >= 1 },
              { label: "Max Drawdown", value: `${result.max_drawdown_pct.toFixed(2)}%`, positive: false },
              { label: "Profit Factor", value: result.profit_factor.toFixed(2), positive: result.profit_factor >= 1 },
              { label: "Total Trades", value: String(result.total_trades), neutral: true },
              { label: "Annualised Return", value: result.annualised_return_pct ? `${result.annualised_return_pct.toFixed(2)}%` : "—", positive: (result.annualised_return_pct ?? 0) >= 0 },
              { label: "Period", value: `${result.start_date} → ${result.end_date}`, neutral: true },
            ].map(s => (
              <div key={s.label} className="panel p-4 space-y-1">
                <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{s.label}</p>
                <p className="mono text-base font-bold" style={{ color: s.neutral ? "var(--text-primary)" : s.positive ? "var(--green)" : "var(--red)" }}>
                  {s.value}
                </p>
              </div>
            ))}
          </div>

          {/* Equity curve */}
          {result.equity_curve?.length > 0 && (
            <div className="panel p-5">
              <p className="text-xs font-semibold mb-4" style={{ color: "var(--text-primary)" }}>Equity Curve</p>
              <div className="h-40 flex items-end gap-px">
                {result.equity_curve.map((pt, i) => {
                  const min = Math.min(...result.equity_curve.map(p => p.equity));
                  const max = Math.max(...result.equity_curve.map(p => p.equity));
                  const pct = max === min ? 50 : ((pt.equity - min) / (max - min)) * 100;
                  return (
                    <div
                      key={i}
                      className="flex-1 rounded-sm"
                      style={{
                        height: `${pct}%`,
                        background: pt.equity >= result.equity_curve[0]?.equity ? "var(--green)" : "var(--red)",
                        opacity: 0.8,
                      }}
                      title={`${pt.date}: $${pt.equity.toFixed(2)}`}
                    />
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
