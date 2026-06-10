"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PieChart } from "lucide-react";
import { api } from "@/lib/api";
import type { AllocationStrategy } from "@/types/api";

const METHODS = [
  { value: "risk_parity", label: "Risk Parity" },
  { value: "inverse_vol", label: "Inverse Vol" },
  { value: "max_sharpe", label: "Max Sharpe" },
  { value: "equal_weight", label: "Equal" },
] as const;

const BARS = ["var(--blue)", "var(--purple)", "var(--green)", "var(--orange)", "var(--red)", "#0ea5e9"];

function fmt(n: number | null | undefined, dp = 2): string {
  return n == null ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function AllocationPanel({ days }: { days: number }) {
  const [method, setMethod] = useState<string>("risk_parity");
  const [capital, setCapital] = useState(5000);

  const { data, isLoading } = useQuery({
    queryKey: ["journal", "allocation", days, method, capital],
    queryFn: () => api.journal.allocation(days, method, capital),
    refetchInterval: 60_000,
  });

  const strategies: AllocationStrategy[] = (data?.strategies ?? []).filter((s) => s.weight > 0);

  return (
    <div className="panel">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-2">
          <PieChart size={14} style={{ color: "var(--blue)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Capital Allocation</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-0.5">
            {METHODS.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => setMethod(m.value)}
                className="text-[10px] mono px-1.5 py-1 rounded"
                style={m.value === method ? { background: "var(--blue-dim)", color: "var(--blue)" } : { color: "var(--text-tertiary)" }}
              >
                {m.label}
              </button>
            ))}
          </div>
          <input
            type="number"
            value={capital}
            onChange={(e) => setCapital(Number(e.target.value))}
            className="mono text-[11px] px-2 py-1 rounded w-24"
            style={{ background: "var(--bg-surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            title="Total capital to split"
          />
        </div>
      </div>

      <div className="p-4 space-y-4">
        {isLoading ? (
          <div className="h-16 animate-pulse rounded" style={{ background: "var(--bg-surface-2)" }} />
        ) : strategies.length === 0 ? (
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            No strategies with usable return history yet ({data?.usable_count ?? 0} usable).
          </p>
        ) : (
          <>
            {/* Weight bar */}
            <div className="flex h-6 rounded overflow-hidden" style={{ border: "1px solid var(--border)" }}>
              {strategies.map((s, i) => (
                <div
                  key={s.strategy_type}
                  style={{ width: `${s.weight * 100}%`, background: BARS[i % BARS.length] }}
                  title={`${s.strategy_type} ${fmt(s.weight * 100, 1)}%`}
                />
              ))}
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    {["Strategy", "Weight", "Capital", "Daily Vol", "Risk Contrib"].map((h, i) => (
                      <th key={h} className={`px-3 py-2 font-medium ${i > 0 ? "text-right" : "text-left"}`} style={{ color: "var(--text-tertiary)" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {strategies.map((s, i) => (
                    <tr key={s.strategy_type} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                      <td className="px-3 py-2">
                        <span className="inline-flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
                          <span className="inline-block w-2 h-2 rounded-sm" style={{ background: BARS[i % BARS.length] }} />
                          {s.strategy_type.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="px-3 py-2 mono text-right font-semibold" style={{ color: "var(--text-primary)" }}>{fmt(s.weight * 100, 1)}%</td>
                      <td className="px-3 py-2 mono text-right" style={{ color: "var(--text-secondary)" }}>${fmt(s.capital, 0)}</td>
                      <td className="px-3 py-2 mono text-right" style={{ color: "var(--text-tertiary)" }}>{fmt(s.daily_vol)}</td>
                      <td className="px-3 py-2 mono text-right" style={{ color: "var(--text-tertiary)" }}>{s.risk_contribution == null ? "—" : `${fmt(s.risk_contribution * 100, 1)}%`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              From date-aligned daily realised-P&L over {days}d. Risk parity equalises each strategy&apos;s risk contribution.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
