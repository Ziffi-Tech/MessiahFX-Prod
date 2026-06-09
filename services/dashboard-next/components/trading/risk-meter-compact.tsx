"use client";

import { Shield, AlertTriangle } from "lucide-react";
import { useRiskState } from "@/lib/hooks";

export function RiskMeterCompact() {
  const { data: risk } = useRiskState();

  const drawdownPct = risk?.daily_drawdown_pct ?? 0;
  const maxPct      = risk?.max_daily_drawdown_pct ?? 3;
  const fillPct     = Math.min((drawdownPct / maxPct) * 100, 100);
  const isDanger    = fillPct > 70;
  const barColour   =
    fillPct < 50 ? "var(--green)" : fillPct < 75 ? "var(--orange)" : "var(--red)";

  return (
    <div className="panel p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Shield size={13} style={{ color: isDanger ? "var(--red)" : "var(--blue)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
            Risk Monitor
          </span>
        </div>
        <div className="flex items-center gap-2">
          {risk?.kill_switch_active && (
            <span className="badge badge-red">KILLED</span>
          )}
          {risk?.in_cooldown && !risk.kill_switch_active && (
            <span className="badge badge-orange">COOLDOWN</span>
          )}
        </div>
      </div>

      {/* Drawdown bar */}
      <div className="space-y-1">
        <div className="flex justify-between text-xs">
          <span style={{ color: "var(--text-secondary)" }}>Daily Drawdown</span>
          <span className="mono" style={{ color: barColour }}>
            {drawdownPct.toFixed(2)}% / {maxPct}%
          </span>
        </div>
        <div
          className="h-1.5 rounded-full overflow-hidden"
          style={{ background: "var(--bg-surface-3)" }}
        >
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${fillPct}%`, background: barColour }}
          />
        </div>
      </div>

      {/* Consecutive losses */}
      <div className="flex justify-between text-xs">
        <span style={{ color: "var(--text-secondary)" }}>Consec. Losses</span>
        <span
          className="mono"
          style={{
            color:
              (risk?.consecutive_losses ?? 0) >= 3
                ? "var(--orange)"
                : "var(--text-primary)",
          }}
        >
          {risk?.consecutive_losses ?? 0} / {risk?.max_consecutive_losses ?? 5}
        </span>
      </div>

      {/* Open positions */}
      <div className="flex justify-between text-xs">
        <span style={{ color: "var(--text-secondary)" }}>Open Positions</span>
        <span className="mono" style={{ color: "var(--text-primary)" }}>
          {risk?.open_positions ?? 0} / {risk?.max_open_positions ?? 5}
        </span>
      </div>

      {isDanger && (
        <div
          className="flex items-center gap-2 px-3 py-2 rounded text-xs"
          style={{ background: "var(--red-dim)", color: "var(--red)" }}
        >
          <AlertTriangle size={12} />
          Approaching drawdown limit
        </div>
      )}
    </div>
  );
}
