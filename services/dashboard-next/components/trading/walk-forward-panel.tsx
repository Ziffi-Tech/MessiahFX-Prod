"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Repeat, Play } from "lucide-react";
import { api } from "@/lib/api";
import type { WfaFold } from "@/types/api";

function verdictBadge(v?: string): string {
  if (v === "robust") return "badge-green";
  if (v === "overfit") return "badge-red";
  if (v === "marginal") return "badge-orange";
  return "badge-gray";
}

function fmt(n: number | null | undefined, dp = 2): string {
  return n == null ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

const inputStyle: React.CSSProperties = {
  background: "var(--bg-surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)",
};

export function WalkForwardPanel() {
  const [spot, setSpot] = useState("BTC/USDT");
  const [perp, setPerp] = useState("BTC/USDT:USDT");
  const [interval, setInterval] = useState("1h");
  const [days, setDays] = useState(180);

  const run = useMutation({
    mutationFn: () =>
      api.backtest.walkForwardStatArb({ spot_symbol: spot, perp_symbol: perp, interval, days }),
  });

  const res = run.data;
  const summary = res?.summary;
  const folds: WfaFold[] = res?.folds ?? [];

  return (
    <div className="panel">
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-2">
          <Repeat size={14} style={{ color: "var(--purple)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
            Walk-Forward Validation (stat-arb)
          </span>
        </div>
        {summary && <span className={`badge ${verdictBadge(summary.verdict)}`}>{summary.verdict.toUpperCase()}</span>}
      </div>

      <div className="p-4 space-y-4">
        {/* Controls */}
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
            <span>Spot</span>
            <input value={spot} onChange={(e) => setSpot(e.target.value)} className="block mono text-xs px-2 py-1.5 rounded w-32" style={inputStyle} />
          </label>
          <label className="text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
            <span>Perp</span>
            <input value={perp} onChange={(e) => setPerp(e.target.value)} className="block mono text-xs px-2 py-1.5 rounded w-36" style={inputStyle} />
          </label>
          <label className="text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
            <span>Interval</span>
            <input value={interval} onChange={(e) => setInterval(e.target.value)} className="block mono text-xs px-2 py-1.5 rounded w-20" style={inputStyle} />
          </label>
          <label className="text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
            <span>Days</span>
            <input type="number" value={days} onChange={(e) => setDays(Number(e.target.value))} className="block mono text-xs px-2 py-1.5 rounded w-20" style={inputStyle} />
          </label>
          <button
            type="button"
            onClick={() => run.mutate()}
            disabled={run.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold disabled:opacity-50"
            style={{ background: "var(--blue)", color: "#fff" }}
          >
            <Play size={12} />
            {run.isPending ? "Running…" : "Run"}
          </button>
        </div>

        {/* Result */}
        {run.isError && <p className="text-xs" style={{ color: "var(--red)" }}>Walk-forward request failed.</p>}
        {res?.status === "insufficient_data" && (
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            Not enough persisted OHLCV: {res.spot_candles} spot / {res.perp_candles} perp candles. {res.detail}
          </p>
        )}

        {summary && res?.status === "ok" && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
              <Stat label="Folds (OOS)" value={String(summary.folds)} />
              <Stat label="Median OOS Sharpe" value={fmt(summary.median_oos_sharpe)} color={(summary.median_oos_sharpe ?? 0) > 0 ? "var(--green)" : "var(--red)"} />
              <Stat label="Walk-Fwd Efficiency" value={fmt(summary.walk_forward_efficiency)} color={(summary.walk_forward_efficiency ?? 0) >= 0.5 ? "var(--green)" : "var(--orange)"} />
              <Stat label="Profitable folds" value={`${fmt((summary.positive_fold_fraction ?? 0) * 100, 0)}%`} />
            </div>

            {folds.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border)" }}>
                      {["OOS window", "Params (w/z)", "IS Sharpe", "OOS Sharpe", "OOS P&L", "Trades"].map((h, i) => (
                        <th key={h} className={`px-3 py-2 font-medium ${i > 1 ? "text-right" : "text-left"}`} style={{ color: "var(--text-tertiary)" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {folds.map((f, i) => (
                      <tr key={i} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                        <td className="px-3 py-2 mono" style={{ color: "var(--text-secondary)" }}>{(f.oos_start_dt || "").slice(0, 10)}</td>
                        <td className="px-3 py-2 mono" style={{ color: "var(--text-secondary)" }}>{f.params.window}/{f.params.entry_z}</td>
                        <td className="px-3 py-2 mono text-right" style={{ color: "var(--text-tertiary)" }}>{fmt(f.is_sharpe)}</td>
                        <td className="px-3 py-2 mono text-right" style={{ color: f.oos_sharpe > 0 ? "var(--green)" : "var(--red)" }}>{fmt(f.oos_sharpe)}</td>
                        <td className="px-3 py-2 mono text-right" style={{ color: f.oos_net_pnl >= 0 ? "var(--green)" : "var(--red)" }}>{f.oos_net_pnl >= 0 ? "+" : ""}{fmt(f.oos_net_pnl)}</td>
                        <td className="px-3 py-2 mono text-right" style={{ color: "var(--text-secondary)" }}>{f.oos_trades}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded p-2.5" style={{ background: "var(--bg-surface-2)" }}>
      <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
      <div className="mono text-sm font-bold" style={{ color: color ?? "var(--text-primary)" }}>{value}</div>
    </div>
  );
}
