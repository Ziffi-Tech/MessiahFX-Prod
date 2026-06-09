"use client";

import { Zap } from "lucide-react";
import { useSignals } from "@/lib/hooks";
import type { Opportunity } from "@/types/api";

function statusBadge(s: Opportunity) {
  if (s.executed)              return <span className="badge badge-green">FILLED</span>;
  if (s.expired)               return <span className="badge badge-gray">EXPIRED</span>;
  if (s.risk_approved === false) return <span className="badge badge-red">REJECTED</span>;
  return                              <span className="badge badge-blue">PENDING</span>;
}

export function RecentSignals() {
  const { data, isLoading } = useSignals(15);
  const signals = data?.opportunities ?? [];

  return (
    <div className="panel">
      <div
        className="flex items-center justify-between px-4 py-3 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <div className="flex items-center gap-2">
          <Zap size={13} style={{ color: "var(--orange)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
            Recent Signals
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="live-dot" />
          <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            LIVE
          </span>
        </div>
      </div>

      {isLoading ? (
        <div className="p-4 space-y-2">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-8 rounded animate-pulse"
              style={{ background: "var(--bg-surface-2)" }}
            />
          ))}
        </div>
      ) : signals.length === 0 ? (
        <div className="p-8 text-center">
          <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>
            No signals recorded yet — waiting for market data
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border)" }}>
                {["Time", "Strategy", "Symbol", "Edge bps", "R:R", "AI Score", "Status"].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-2 text-left font-medium"
                    style={{ color: "var(--text-tertiary)" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => {
                const rs = s as Opportunity & { rr_ratio?: number };
                return (
                  <tr
                    key={s.id}
                    className="transition-colors hover:bg-[var(--bg-hover)]"
                    style={{ borderBottom: "1px solid var(--border-subtle)" }}
                  >
                    <td className="px-4 py-2 mono" style={{ color: "var(--text-tertiary)" }}>
                      {new Date(s.detected_at).toLocaleTimeString("en-GB", { hour12: false })}
                    </td>
                    <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>
                      {s.strategy_type.replace(/_/g, " ").toUpperCase()}
                    </td>
                    <td className="px-4 py-2 mono" style={{ color: "var(--blue)" }}>
                      {s.symbol_primary}
                    </td>
                    <td
                      className="px-4 py-2 mono"
                      style={{
                        color: (s.net_edge_bps ?? 0) >= 0 ? "var(--green)" : "var(--red)",
                      }}
                    >
                      {s.net_edge_bps?.toFixed(1) ?? "—"}
                    </td>
                    <td className="px-4 py-2 mono" style={{ color: "var(--text-secondary)" }}>
                      {rs.rr_ratio != null ? `${rs.rr_ratio.toFixed(1)}:1` : "—"}
                    </td>
                    <td className="px-4 py-2">
                      {s.ai_score !== null ? (
                        <span
                          className="mono font-bold"
                          style={{
                            color:
                              s.ai_score >= 70
                                ? "var(--green)"
                                : s.ai_score >= 40
                                ? "var(--orange)"
                                : "var(--red)",
                          }}
                        >
                          {s.ai_score}
                        </span>
                      ) : (
                        <span style={{ color: "var(--text-tertiary)" }}>—</span>
                      )}
                    </td>
                    <td className="px-4 py-2">{statusBadge(s)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
