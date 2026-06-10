"use client";

import { AlertOctagon, Power } from "lucide-react";
import { useRiskState, useKillSwitch } from "@/lib/hooks";
import { ReadinessPanel } from "@/components/trading/readiness-panel";

function Gauge({ label, current, max, suffix = "", decimals = 0 }: {
  label: string; current: number; max: number; suffix?: string; decimals?: number;
}) {
  const pct = max > 0 ? Math.min((current / max) * 100, 100) : 0;
  const barColor = pct < 50 ? "var(--green)" : pct < 80 ? "var(--orange)" : "var(--red)";
  return (
    <div className="panel p-5 space-y-3">
      <div className="flex justify-between text-xs">
        <span style={{ color: "var(--text-secondary)" }}>{label}</span>
        <span className="mono font-semibold" style={{ color: "var(--text-primary)" }}>
          {current.toFixed(decimals)}{suffix} / {max}{suffix}
        </span>
      </div>
      <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-surface-3)" }}>
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: barColor }}
        />
      </div>
      <div className="mono text-2xl font-bold" style={{ color: barColor }}>
        {current.toFixed(decimals)}{suffix}
      </div>
    </div>
  );
}

export default function RiskPage() {
  const { data: risk, isLoading } = useRiskState();
  const ks = useKillSwitch();

  if (isLoading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="panel h-20 animate-pulse" />
        ))}
      </div>
    );
  }

  const handleKillSwitch = (activate: boolean) => {
    if (activate && !confirm("Activate kill switch? This will halt all trading immediately.")) return;
    ks.mutate(activate);
  };

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div />
        {risk?.kill_switch_active ? (
          <button
            onClick={() => handleKillSwitch(false)}
            disabled={ks.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded text-sm font-semibold disabled:opacity-50"
            style={{
              background: "var(--green-dim)",
              color: "var(--green)",
              border: "1px solid rgba(0,229,160,0.3)",
            }}
          >
            <Power size={14} />
            {ks.isPending ? "Resuming…" : "Resume Trading"}
          </button>
        ) : (
          <button
            onClick={() => handleKillSwitch(true)}
            disabled={ks.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded text-sm font-semibold disabled:opacity-50"
            style={{
              background: "var(--red-dim)",
              color: "var(--red)",
              border: "1px solid rgba(255,61,87,0.3)",
            }}
          >
            <AlertOctagon size={14} />
            {ks.isPending ? "Activating…" : "Kill Switch"}
          </button>
        )}
      </div>

      {risk?.kill_switch_active && (
        <div
          className="flex items-center gap-3 px-5 py-4 rounded-lg"
          style={{ background: "var(--red-dim)", border: "1px solid var(--red)" }}
        >
          <AlertOctagon size={18} style={{ color: "var(--red)" }} />
          <div>
            <p className="text-sm font-semibold" style={{ color: "var(--red)" }}>
              Kill switch active — all trading halted
            </p>
            <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
              Click &ldquo;Resume Trading&rdquo; to re-enable execution
            </p>
          </div>
        </div>
      )}

      {/* Gauges */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Gauge
          label="Daily Drawdown"
          current={risk?.daily_drawdown_pct ?? 0}
          max={risk?.max_daily_drawdown_pct ?? 3}
          suffix="%"
          decimals={2}
        />
        <Gauge
          label="Consecutive Losses"
          current={risk?.consecutive_losses ?? 0}
          max={risk?.max_consecutive_losses ?? 5}
        />
        <Gauge
          label="Open Positions"
          current={risk?.open_positions ?? 0}
          max={risk?.max_open_positions ?? 5}
        />
      </div>

      {/* Status table */}
      <div className="panel p-5 space-y-3">
        <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
          System Status
        </span>
        <div className="grid grid-cols-2 gap-3 text-xs">
          {[
            {
              label: "Trading Mode",
              value: risk?.trading_mode?.toUpperCase() ?? "—",
              badge: risk?.trading_mode === "paper" ? "badge-orange" : risk?.trading_mode === "live" ? "badge-green" : "badge-red",
            },
            {
              label: "Kill Switch",
              value: risk?.kill_switch_active ? "ACTIVE" : "INACTIVE",
              badge: risk?.kill_switch_active ? "badge-red" : "badge-green",
            },
            {
              label: "Cooldown",
              value: risk?.in_cooldown ? "IN COOLDOWN" : "CLEAR",
              badge: risk?.in_cooldown ? "badge-orange" : "badge-green",
            },
            {
              label: "Cooldown Until",
              value: risk?.cooldown_until
                ? new Date(risk.cooldown_until).toLocaleTimeString()
                : "N/A",
              badge: "badge-gray",
            },
          ].map((item) => (
            <div
              key={item.label}
              className="flex items-center justify-between py-2 border-b"
              style={{ borderColor: "var(--border-subtle)" }}
            >
              <span style={{ color: "var(--text-secondary)" }}>{item.label}</span>
              <span className={`badge ${item.badge}`}>{item.value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Go-live readiness gate */}
      <ReadinessPanel />
    </div>
  );
}
