"use client";

import { useEffect, useState } from "react";
import type { Trade, PnLSummary } from "@/types/api";
import { TrendingUp, TrendingDown, Activity } from "lucide-react";

export default function PositionsPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [pnl, setPnl] = useState<PnLSummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      const [t, p] = await Promise.all([
        fetch("/api/gateway/journal/trades?status=open&limit=50").then(r => r.json()).catch(() => ({ trades: [] })),
        fetch("/api/gateway/journal/pnl").then(r => r.json()).catch(() => null),
      ]);
      setTrades(t.trades ?? []);
      setPnl(p);
      setLoading(false);
    };
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, []);

  const stats = [
    { label: "Total P&L", value: pnl ? `$${pnl.total_realized_pnl.toFixed(2)}` : "—", positive: (pnl?.total_realized_pnl ?? 0) >= 0 },
    { label: "Win Rate", value: pnl ? `${(pnl.win_rate * 100).toFixed(1)}%` : "—", neutral: true },
    { label: "Profit Factor", value: pnl ? pnl.profit_factor.toFixed(2) : "—", positive: (pnl?.profit_factor ?? 0) >= 1 },
    { label: "Max Drawdown", value: pnl ? `${pnl.max_drawdown_pct.toFixed(2)}%` : "—", positive: false },
  ];

  return (
    <div className="space-y-5">
      <h1 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>
        Positions & P&L
      </h1>

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {stats.map(s => (
          <div key={s.label} className="panel p-4 space-y-1.5">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{s.label}</span>
            <div
              className="mono text-lg font-bold"
              style={{ color: s.neutral ? "var(--text-primary)" : s.positive ? "var(--green)" : "var(--red)" }}
            >
              {s.value}
            </div>
          </div>
        ))}
      </div>

      {/* Trade table */}
      <div className="panel">
        <div className="px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
            Open Positions
          </span>
        </div>
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-8 rounded animate-pulse" style={{ background: "var(--bg-surface-2)" }} />
            ))}
          </div>
        ) : trades.length === 0 ? (
          <div className="p-8 text-center">
            <Activity size={24} className="mx-auto mb-3" style={{ color: "var(--text-tertiary)" }} />
            <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>
              No open positions
            </p>
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr style={{ borderBottom: `1px solid var(--border)` }}>
                {["Symbol", "Side", "Qty", "Entry", "Current", "P&L", "Strategy", "Opened"].map(h => (
                  <th key={h} className="px-4 py-2 text-left font-medium" style={{ color: "var(--text-tertiary)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map(t => (
                <tr
                  key={t.id}
                  className="hover:bg-[var(--bg-hover)] transition-colors"
                  style={{ borderBottom: `1px solid var(--border-subtle)` }}
                >
                  <td className="px-4 py-2 mono font-semibold" style={{ color: "var(--blue)" }}>{t.symbol}</td>
                  <td className="px-4 py-2">
                    <span className={`badge ${t.side === "buy" ? "badge-blue" : "badge-orange"}`}>
                      {t.side.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-2 mono">{t.quantity}</td>
                  <td className="px-4 py-2 mono">{t.average_fill_price?.toFixed(5) ?? "—"}</td>
                  <td className="px-4 py-2 mono">—</td>
                  <td className="px-4 py-2 mono" style={{ color: (t.realized_pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                    {t.realized_pnl !== null ? `$${t.realized_pnl.toFixed(2)}` : "—"}
                  </td>
                  <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>{t.strategy_type ?? "—"}</td>
                  <td className="px-4 py-2 mono" style={{ color: "var(--text-tertiary)" }}>
                    {new Date(t.opened_at).toLocaleTimeString("en-GB", { hour12: false })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
