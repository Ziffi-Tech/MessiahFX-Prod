"use client";

import { useEffect, useState } from "react";
import { Zap } from "lucide-react";
import type { Opportunity } from "@/types/api";

export function RecentSignals() {
  const [signals, setSignals] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/gateway/journal/opportunities?limit=10", { cache: "no-store" });
        if (res.ok) {
          const json = await res.json();
          setSignals(json.opportunities ?? []);
        }
      } catch { /* */ } finally {
        setLoading(false);
      }
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []);

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
        <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          Auto-refreshes every 15s
        </span>
      </div>

      {loading ? (
        <div className="p-4 space-y-2">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-8 rounded animate-pulse" style={{ background: "var(--bg-surface-2)" }} />
          ))}
        </div>
      ) : signals.length === 0 ? (
        <div className="p-8 text-center">
          <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>
            No signals recorded yet — waiting for market data
          </p>
        </div>
      ) : (
        <table className="w-full text-xs">
          <thead>
            <tr style={{ borderBottom: `1px solid var(--border)` }}>
              {["Time", "Strategy", "Symbol", "Edge bps", "AI Score", "Status"].map(h => (
                <th key={h} className="px-4 py-2 text-left font-medium" style={{ color: "var(--text-tertiary)" }}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {signals.map((s) => (
              <tr
                key={s.id}
                className="transition-colors hover:bg-[var(--bg-hover)]"
                style={{ borderBottom: `1px solid var(--border-subtle)` }}
              >
                <td className="px-4 py-2 mono" style={{ color: "var(--text-tertiary)" }}>
                  {new Date(s.detected_at).toLocaleTimeString("en-GB", { hour12: false })}
                </td>
                <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>
                  {s.strategy_type.replace("_", " ").toUpperCase()}
                </td>
                <td className="px-4 py-2 mono" style={{ color: "var(--blue)" }}>
                  {s.symbol_primary}
                </td>
                <td className="px-4 py-2 mono" style={{ color: (s.net_edge_bps ?? 0) >= 0 ? "var(--green)" : "var(--red)" }}>
                  {s.net_edge_bps?.toFixed(1) ?? "—"}
                </td>
                <td className="px-4 py-2">
                  {s.ai_score !== null ? (
                    <span
                      className="mono font-bold"
                      style={{
                        color: s.ai_score >= 70 ? "var(--green)"
                          : s.ai_score >= 40 ? "var(--orange)"
                          : "var(--red)"
                      }}
                    >
                      {s.ai_score}
                    </span>
                  ) : (
                    <span style={{ color: "var(--text-tertiary)" }}>—</span>
                  )}
                </td>
                <td className="px-4 py-2">
                  {s.executed ? (
                    <span className="badge badge-green">EXECUTED</span>
                  ) : s.expired ? (
                    <span className="badge badge-gray">EXPIRED</span>
                  ) : s.risk_approved === false ? (
                    <span className="badge badge-red">REJECTED</span>
                  ) : (
                    <span className="badge badge-blue">PENDING</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
