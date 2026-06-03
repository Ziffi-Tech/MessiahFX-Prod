"use client";

import { useEffect, useState } from "react";
import type { Trade, PnLSummary } from "@/types/api";
import { ScrollText, Download } from "lucide-react";

export default function JournalPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [pnl, setPnl] = useState<PnLSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("all");

  useEffect(() => {
    const load = async () => {
      const status = filter === "all" ? "" : `&status=${filter}`;
      const [t, p] = await Promise.all([
        fetch(`/api/gateway/journal/trades?limit=100${status}`, { cache: "no-store" }).then(r => r.json()).catch(() => ({ trades: [] })),
        fetch("/api/gateway/journal/pnl", { cache: "no-store" }).then(r => r.json()).catch(() => null),
      ]);
      setTrades(t.trades ?? []);
      setPnl(p);
      setLoading(false);
    };
    load();
  }, [filter]);

  const exportCsv = () => {
    const headers = "Symbol,Side,Qty,Entry,Exit,PnL,Strategy,Opened,Status\n";
    const rows = trades.map(t =>
      [t.symbol, t.side, t.quantity, t.average_fill_price ?? "", "", t.realized_pnl ?? "", t.strategy_type ?? "", t.opened_at, t.status].join(",")
    ).join("\n");
    const blob = new Blob([headers + rows], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `mezna-trades-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>Trade Journal</h1>
        <button
          onClick={exportCsv}
          className="flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}
        >
          <Download size={12} />
          Export CSV
        </button>
      </div>

      {/* PnL summary */}
      {pnl && (
        <div className="grid grid-cols-3 lg:grid-cols-6 gap-3">
          {[
            { label: "Total P&L", v: `$${pnl.total_realized_pnl.toFixed(2)}`, positive: pnl.total_realized_pnl >= 0 },
            { label: "Win Rate", v: `${(pnl.win_rate * 100).toFixed(1)}%`, neutral: true },
            { label: "Profit Factor", v: pnl.profit_factor.toFixed(2), positive: pnl.profit_factor >= 1 },
            { label: "Avg Win", v: `$${pnl.average_win.toFixed(2)}`, positive: true },
            { label: "Avg Loss", v: `$${pnl.average_loss.toFixed(2)}`, positive: false },
            { label: "Trades", v: String(pnl.total_trades), neutral: true },
          ].map(s => (
            <div key={s.label} className="panel p-3 space-y-1">
              <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{s.label}</p>
              <p className="mono text-sm font-bold" style={{ color: s.neutral ? "var(--text-primary)" : s.positive ? "var(--green)" : "var(--red)" }}>
                {s.v}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Filter */}
      <div className="flex gap-2">
        {["all", "filled", "open", "cancelled", "rejected"].map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className="px-3 py-1.5 rounded text-xs font-medium transition-colors"
            style={{
              background: filter === f ? "var(--blue-dim)" : "transparent",
              color: filter === f ? "var(--blue)" : "var(--text-secondary)",
              border: `1px solid ${filter === f ? "rgba(14,165,233,0.3)" : "var(--border)"}`,
            }}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="panel overflow-hidden">
        {loading ? (
          <div className="p-4 space-y-2">
            {[...Array(5)].map((_, i) => <div key={i} className="h-8 rounded animate-pulse" style={{ background: "var(--bg-surface-2)" }} />)}
          </div>
        ) : trades.length === 0 ? (
          <div className="p-8 text-center">
            <ScrollText size={24} className="mx-auto mb-3" style={{ color: "var(--text-tertiary)" }} />
            <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>No trades recorded yet</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ borderBottom: `1px solid var(--border)` }}>
                  {["Symbol", "Side", "Qty", "Entry Price", "P&L", "Strategy", "Status", "Date"].map(h => (
                    <th key={h} className="px-4 py-2 text-left font-medium" style={{ color: "var(--text-tertiary)" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map(t => (
                  <tr key={t.id} className="hover:bg-[var(--bg-hover)] transition-colors" style={{ borderBottom: `1px solid var(--border-subtle)` }}>
                    <td className="px-4 py-2 mono font-semibold" style={{ color: "var(--blue)" }}>{t.symbol}</td>
                    <td className="px-4 py-2">
                      <span className={`badge ${t.side === "buy" ? "badge-blue" : "badge-orange"}`}>{t.side.toUpperCase()}</span>
                    </td>
                    <td className="px-4 py-2 mono">{t.quantity}</td>
                    <td className="px-4 py-2 mono">{t.average_fill_price?.toFixed(5) ?? "—"}</td>
                    <td className="px-4 py-2 mono font-semibold" style={{ color: (t.realized_pnl ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                      {t.realized_pnl !== null ? `${t.realized_pnl >= 0 ? "+" : ""}$${t.realized_pnl.toFixed(2)}` : "—"}
                    </td>
                    <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>{t.strategy_type ?? "—"}</td>
                    <td className="px-4 py-2">
                      <span className={`badge ${t.status === "filled" ? "badge-green" : t.status === "open" ? "badge-blue" : t.status === "rejected" ? "badge-red" : "badge-gray"}`}>
                        {t.status.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-2 mono" style={{ color: "var(--text-tertiary)" }}>
                      {new Date(t.opened_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
