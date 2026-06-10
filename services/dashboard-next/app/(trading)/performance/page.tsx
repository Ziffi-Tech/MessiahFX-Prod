"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { ReadinessPanel } from "@/components/trading/readiness-panel";
import type { StrategyPerformance, TcaRow } from "@/types/api";

const WINDOWS = [7, 30, 90] as const;

function fmt(n: number | null | undefined, dp = 2, dash = "—"): string {
  if (n == null) return dash;
  return n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}
function pnlColor(n: number): string {
  return n > 0 ? "var(--green)" : n < 0 ? "var(--red)" : "var(--text-secondary)";
}
function ratioColor(n: number | null): string {
  if (n == null) return "var(--text-tertiary)";
  return n >= 1 ? "var(--green)" : n >= 0 ? "var(--orange)" : "var(--red)";
}

function Th({ children, right = false }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={`px-3 py-2 font-medium ${right ? "text-right" : "text-left"}`} style={{ color: "var(--text-tertiary)" }}>
      {children}
    </th>
  );
}
function Td({ children, right = false, color }: { children: React.ReactNode; right?: boolean; color?: string }) {
  return (
    <td className={`px-3 py-2 mono ${right ? "text-right" : "text-left"}`} style={{ color: color ?? "var(--text-secondary)" }}>
      {children}
    </td>
  );
}

export default function PerformancePage() {
  const [days, setDays] = useState<(typeof WINDOWS)[number]>(30);

  const perf = useQuery({
    queryKey: ["journal", "by-strategy", days],
    queryFn: () => api.journal.byStrategy(days),
    refetchInterval: 30_000,
  });
  const tca = useQuery({
    queryKey: ["journal", "tca", days],
    queryFn: () => api.journal.tca(days),
    refetchInterval: 30_000,
  });

  const strategies: StrategyPerformance[] = perf.data?.strategies ?? [];
  const tcaRows: TcaRow[] = tca.data?.rows ?? [];

  return (
    <div className="space-y-5">
      {/* Window selector */}
      <div className="flex items-center justify-between">
        <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
          Paper-validation analytics — judge each strategy &ldquo;good&rdquo;, not just green.
        </p>
        <div className="flex items-center gap-0.5">
          {WINDOWS.map((w) => (
            <button
              key={w}
              type="button"
              onClick={() => setDays(w)}
              className="text-[11px] mono px-2 py-1 rounded"
              style={w === days ? { background: "var(--blue-dim)", color: "var(--blue)" } : { color: "var(--text-tertiary)" }}
            >
              {w}d
            </button>
          ))}
        </div>
      </div>

      <ReadinessPanel />

      {/* Per-strategy performance */}
      <div className="panel">
        <div className="px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Per-Strategy Performance</span>
        </div>
        {strategies.length === 0 ? (
          <div className="p-6 text-center text-xs" style={{ color: "var(--text-tertiary)" }}>
            {perf.isLoading ? "Loading…" : "No filled trades in this window yet."}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <Th>Strategy</Th><Th right>Trades</Th><Th right>Win %</Th><Th right>Profit Factor</Th>
                  <Th right>Sharpe</Th><Th right>Sortino</Th><Th right>Max DD %</Th>
                  <Th right>Fees</Th><Th right>Realized P&amp;L</Th>
                </tr>
              </thead>
              <tbody>
                {strategies.map((s) => (
                  <tr key={s.strategy_type} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                    <Td color="var(--text-primary)">{s.strategy_type.replace(/_/g, " ")}</Td>
                    <Td right>{s.filled_trades}</Td>
                    <Td right>{fmt(s.win_rate * 100, 1)}%</Td>
                    <Td right color={ratioColor(s.profit_factor)}>{fmt(s.profit_factor, 2)}</Td>
                    <Td right color={ratioColor(s.sharpe_ratio)}>{fmt(s.sharpe_ratio, 2)}</Td>
                    <Td right color={ratioColor(s.sortino_ratio)}>{fmt(s.sortino_ratio, 2)}</Td>
                    <Td right color={s.max_drawdown_pct > 0 ? "var(--orange)" : "var(--text-secondary)"}>{fmt(s.max_drawdown_pct, 2)}</Td>
                    <Td right>{fmt(s.total_fees, 2)}</Td>
                    <Td right color={pnlColor(s.realized_pnl)}>{s.realized_pnl >= 0 ? "+" : ""}{fmt(s.realized_pnl, 2)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Transaction-cost analysis */}
      <div className="panel">
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Transaction-Cost Analysis</span>
          {tca.data && (
            <span className="text-[11px] mono" style={{ color: "var(--text-tertiary)" }}>
              total {fmt(tca.data.totals.fee_bps, 2)} bps · ${fmt(tca.data.totals.total_fees, 2)}
            </span>
          )}
        </div>
        {tcaRows.length === 0 ? (
          <div className="p-6 text-center text-xs" style={{ color: "var(--text-tertiary)" }}>
            {tca.isLoading ? "Loading…" : "No fills in this window yet."}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)" }}>
                  <Th>Strategy</Th><Th>Venue</Th><Th right>Fills</Th><Th right>Notional</Th>
                  <Th right>Fee bps</Th><Th right>Avg Slippage bps</Th>
                </tr>
              </thead>
              <tbody>
                {tcaRows.map((r, i) => (
                  <tr key={`${r.strategy_type}-${r.venue}-${i}`} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                    <Td color="var(--text-primary)">{r.strategy_type?.replace(/_/g, " ") ?? "—"}</Td>
                    <Td>{r.venue?.toUpperCase()}</Td>
                    <Td right>{r.fills}</Td>
                    <Td right>${fmt(r.notional, 0)}</Td>
                    <Td right color={r.fee_bps > 10 ? "var(--orange)" : "var(--text-secondary)"}>{fmt(r.fee_bps, 2)}</Td>
                    <Td right color={Math.abs(r.avg_slippage_bps) > 10 ? "var(--orange)" : "var(--text-secondary)"}>{fmt(r.avg_slippage_bps, 2)}</Td>
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
