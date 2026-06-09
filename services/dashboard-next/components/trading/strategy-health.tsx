"use client";

import { Activity, AlertTriangle, TrendingDown, RefreshCw } from "lucide-react";
import { useStrategyOverview } from "@/lib/hooks";
import type { StrategyType } from "@/types/api";

const STRATEGY_LABELS: Record<StrategyType, string> = {
  funding_arb:          "Funding Arb",
  stat_arb:             "Stat Arb",
  swing:                "Swing",
  breakout:             "Breakout",
  mean_reversion_scalp: "MR Scalp",
  momentum:             "Momentum",
};

const ALL: StrategyType[] = [
  "funding_arb", "stat_arb", "swing",
  "breakout", "mean_reversion_scalp", "momentum",
];

function Sparkline({ bits }: { bits: number[] }) {
  if (!bits.length) return <span style={{ color: "var(--text-tertiary)" }}>—</span>;
  return (
    <div className="flex items-end gap-px h-4">
      {bits.map((b, i) => (
        <div
          key={i}
          className="w-1.5 rounded-sm"
          style={{
            height: b ? "100%" : "35%",
            background: b ? "var(--green)" : "var(--red)",
            opacity: 0.85,
          }}
        />
      ))}
    </div>
  );
}

export function StrategyHealth() {
  const { data: overview, isLoading, isFetching, refetch } = useStrategyOverview();

  return (
    <div className="panel">
      <div
        className="flex items-center justify-between px-4 py-3 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="flex items-center gap-2">
          <Activity size={13} style={{ color: "var(--blue)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
            Strategy Health
          </span>
          {overview?.preferred_strategy && (
            <span className="badge badge-green">
              PREFERRED: {STRATEGY_LABELS[overview.preferred_strategy as StrategyType] ?? overview.preferred_strategy}
            </span>
          )}
        </div>
        <button
          onClick={() => void refetch()}
          disabled={isFetching}
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40"
          style={{ color: "var(--text-tertiary)" }}
          aria-label="Refresh strategy health"
        >
          <RefreshCw size={11} className={isFetching ? "animate-spin" : ""} />
        </button>
      </div>

      {isLoading ? (
        <div className="p-4 space-y-2">
          {ALL.map((s) => (
            <div key={s} className="h-10 rounded animate-pulse" style={{ background: "var(--bg-surface-2)" }} />
          ))}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                {["Strategy", "Consec. Losses", "Edge Win Rate", "Last 10", "Drawdown", "Status"].map((h) => (
                  <th key={h} className="px-4 py-2 text-left font-medium" style={{ color: "var(--text-tertiary)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ALL.map((name) => {
                const entry = overview?.strategies?.[name];
                const losses    = entry?.rotation.consecutive_losses ?? 0;
                const degraded  = entry?.rotation.degraded ?? false;
                const threshold = overview?.rotation_threshold ?? 4;
                const winRate   = entry?.edge.win_rate;
                const decayed   = entry?.edge.decayed ?? false;
                const drawdown  = entry?.drawdown.drawdown_pct;
                const recent    = entry?.edge.recent ?? [];
                const isPref    = overview?.preferred_strategy === name;

                let statusEl: React.ReactNode;
                if (degraded) {
                  statusEl = <span className="badge badge-red">DEGRADED</span>;
                } else if (decayed) {
                  statusEl = <span className="badge badge-orange">EDGE DECAY</span>;
                } else if (losses >= threshold - 1) {
                  statusEl = <span className="badge badge-orange">WARNING</span>;
                } else if (isPref) {
                  statusEl = <span className="badge badge-green">PREFERRED</span>;
                } else {
                  statusEl = <span className="badge badge-gray">OK</span>;
                }

                return (
                  <tr
                    key={name}
                    className="transition-colors hover:bg-[var(--bg-hover)]"
                    style={{ borderBottom: "1px solid var(--border-subtle)" }}
                  >
                    <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>
                      {STRATEGY_LABELS[name]}
                    </td>
                    <td className="px-4 py-2">
                      <div className="flex items-center gap-2">
                        <div
                          className="h-1 rounded-full"
                          style={{ width: 40, background: "var(--bg-surface-3)" }}
                        >
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${Math.min((losses / threshold) * 100, 100)}%`,
                              background: losses >= threshold - 1 ? "var(--orange)" : "var(--blue)",
                            }}
                          />
                        </div>
                        <span className="mono" style={{ color: losses >= 3 ? "var(--orange)" : "var(--text-secondary)" }}>
                          {losses}/{threshold}
                        </span>
                        {degraded && <AlertTriangle size={11} style={{ color: "var(--red)" }} />}
                      </div>
                    </td>
                    <td className="px-4 py-2 mono">
                      {winRate != null ? (
                        <span style={{ color: winRate < 0.4 ? "var(--red)" : winRate < 0.5 ? "var(--orange)" : "var(--green)" }}>
                          {(winRate * 100).toFixed(0)}%
                        </span>
                      ) : (
                        <span style={{ color: "var(--text-tertiary)" }}>—</span>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      <Sparkline bits={recent} />
                    </td>
                    <td className="px-4 py-2 mono">
                      {drawdown != null ? (
                        <span
                          className="flex items-center gap-1"
                          style={{ color: drawdown > 3 ? "var(--red)" : drawdown > 1 ? "var(--orange)" : "var(--text-secondary)" }}
                        >
                          {drawdown > 0 && <TrendingDown size={10} />}
                          {drawdown.toFixed(1)}%
                        </span>
                      ) : (
                        <span style={{ color: "var(--text-tertiary)" }}>—</span>
                      )}
                    </td>
                    <td className="px-4 py-2">{statusEl}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {overview && (
        <div
          className="px-4 py-2 border-t text-[10px]"
          style={{ borderColor: "var(--border-subtle)", color: "var(--text-tertiary)" }}
        >
          Regime: <span className="font-medium" style={{ color: "var(--text-secondary)" }}>{overview.current_regime}</span>
          {overview.local_regime && overview.local_regime !== overview.current_regime && (
            <> · local: {overview.local_regime}</>
          )}
          {" "}· Baseline win rate {(overview.baseline_win_rate * 100).toFixed(0)}%
        </div>
      )}
    </div>
  );
}
