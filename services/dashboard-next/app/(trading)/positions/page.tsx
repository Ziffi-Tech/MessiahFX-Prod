"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import type { Trade, PnLSummary } from "@/types/api";
import { useLiveStore } from "@/lib/stores/live";
import { FlashCell } from "@/components/trading/flash-cell";

function fx(symbol: string, venue: string): boolean {
  return venue === "oanda" || symbol.includes("_");
}

export default function PositionsPage() {
  const { data: tradesData, isLoading } = useQuery({
    queryKey: ["journal", "trades", "open"],
    queryFn: () =>
      fetch("/api/gateway/journal/trades?status=open&limit=50", { cache: "no-store" })
        .then((r) => r.json() as Promise<{ trades: Trade[] }>),
    refetchInterval: 8_000,
  });
  const { data: pnl } = useQuery({
    queryKey: ["journal", "pnl", "positions-page"],
    queryFn: () => fetch("/api/gateway/journal/pnl", { cache: "no-store" }).then((r) => r.json() as Promise<PnLSummary>),
    refetchInterval: 8_000,
  });

  const trades = tradesData?.trades ?? [];

  // Live mid per symbol from the SSE tick store (any venue — works for paper too).
  const ticks = useLiveStore((s) => s.ticks);
  const midBySymbol = useMemo(() => {
    const m: Record<string, number> = {};
    for (const t of Object.values(ticks)) {
      if (t.mid != null) m[t.symbol] = t.mid;
    }
    return m;
  }, [ticks]);

  // Live unrealized P&L across open positions.
  const totalUnrealized = useMemo(() => {
    let sum = 0;
    for (const t of trades) {
      const cur = midBySymbol[t.symbol];
      const entry = t.average_fill_price;
      const qty = t.filled_qty || t.quantity;
      if (cur != null && entry != null) sum += (cur - entry) * qty * (t.side === "buy" ? 1 : -1);
    }
    return sum;
  }, [trades, midBySymbol]);

  const stats = [
    { label: "Realized P&L", value: pnl ? `$${(pnl.total_realized_pnl ?? 0).toFixed(2)}` : "—", positive: (pnl?.total_realized_pnl ?? 0) >= 0 },
    { label: "Unrealized P&L", value: `$${totalUnrealized.toFixed(2)}`, positive: totalUnrealized >= 0, live: true },
    { label: "Win Rate", value: pnl ? `${(pnl.win_rate ?? 0).toFixed(1)}%` : "—", neutral: true },
    { label: "Open Positions", value: String(trades.length), neutral: true },
  ];

  return (
    <div className="space-y-5">
      <h1 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>Positions & P&L</h1>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {stats.map((s) => (
          <div key={s.label} className="panel p-4 space-y-1.5">
            <span className="text-xs flex items-center gap-1.5" style={{ color: "var(--text-secondary)" }}>
              {s.label}
              {s.live && <span className="live-dot" />}
            </span>
            <div className="mono text-lg font-bold" style={{ color: s.neutral ? "var(--text-primary)" : s.positive ? "var(--green)" : "var(--red)" }}>
              {s.value}
            </div>
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Open Positions</span>
          <span className="text-[10px] flex items-center gap-1.5" style={{ color: "var(--text-tertiary)" }}>
            <span className="live-dot" /> live prices
          </span>
        </div>

        {isLoading ? (
          <div className="p-4 space-y-2">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-8 rounded animate-pulse" style={{ background: "var(--bg-surface-2)" }} />
            ))}
          </div>
        ) : trades.length === 0 ? (
          <div className="p-8 text-center">
            <Activity size={24} className="mx-auto mb-3" style={{ color: "var(--text-tertiary)" }} />
            <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>No open positions</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  {["Symbol", "Side", "Qty", "Entry", "Current", "Unrealized P&L", "Strategy", "Opened"].map((h, i) => (
                    <th key={h} className={`px-4 py-2 font-medium ${i >= 2 && i <= 5 ? "text-right" : "text-left"}`} style={{ color: "var(--text-tertiary)" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => {
                  const isFx = fx(t.symbol, t.venue);
                  const dp = isFx ? 5 : 2;
                  const cur = midBySymbol[t.symbol] ?? null;
                  const entry = t.average_fill_price;
                  const qty = t.filled_qty || t.quantity;
                  const unrl = cur != null && entry != null ? (cur - entry) * qty * (t.side === "buy" ? 1 : -1) : null;
                  return (
                    <tr key={t.id} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                      <td className="px-4 py-2 mono font-semibold" style={{ color: "var(--blue)" }}>{t.symbol}</td>
                      <td className="px-4 py-2">
                        <span className={`badge ${t.side === "buy" ? "badge-blue" : "badge-orange"}`}>{t.side.toUpperCase()}</span>
                      </td>
                      <td className="px-4 py-2 mono text-right">{qty}</td>
                      <td className="px-4 py-2 mono text-right">{entry?.toFixed(dp) ?? "—"}</td>
                      <td className="px-4 py-2 text-right">
                        <FlashCell value={cur} format={(n) => n.toFixed(dp)} color="var(--text-primary)" />
                      </td>
                      <td className="px-4 py-2 text-right">
                        <FlashCell value={unrl} format={(n) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}`} color={(unrl ?? 0) >= 0 ? "var(--green)" : "var(--red)"} />
                      </td>
                      <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>{t.strategy_type ?? "—"}</td>
                      <td className="px-4 py-2 mono" style={{ color: "var(--text-tertiary)" }}>
                        {new Date(t.opened_at).toLocaleTimeString("en-GB", { hour12: false })}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
