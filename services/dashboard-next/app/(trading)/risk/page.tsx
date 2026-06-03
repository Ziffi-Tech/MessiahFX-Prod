"use client";

import { useEffect, useState } from "react";
import type { RiskState } from "@/types/api";
import { Shield, AlertOctagon, Clock, Power } from "lucide-react";

export default function RiskPage() {
  const [risk, setRisk] = useState<RiskState | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);

  const load = async () => {
    try {
      const res = await fetch("/api/gateway/risk/state", { cache: "no-store" });
      if (res.ok) setRisk(await res.json());
    } catch { /* */ } finally { setLoading(false); }
  };

  useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id); }, []);

  const activateKillSwitch = async () => {
    if (!confirm("Activate kill switch? This will halt all trading immediately.")) return;
    setActing(true);
    await fetch("/api/gateway/risk/kill-switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activate: true }),
    }).catch(() => { });
    await load();
    setActing(false);
  };

  const deactivateKillSwitch = async () => {
    setActing(true);
    await fetch("/api/gateway/risk/kill-switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activate: false }),
    }).catch(() => { });
    await load();
    setActing(false);
  };

  if (loading) return (
    <div className="space-y-3">
      {[1, 2, 3, 4].map(i => <div key={i} className="panel h-20 animate-pulse" />)}
    </div>
  );

  const drawdownFill = risk ? Math.min((risk.daily_drawdown_pct / risk.max_daily_drawdown_pct) * 100, 100) : 0;
  const lossBar = risk ? (risk.consecutive_losses / risk.max_consecutive_losses) * 100 : 0;
  const posBar = risk ? (risk.open_positions / risk.max_open_positions) * 100 : 0;

  const barColor = (pct: number) =>
    pct < 50 ? "var(--green)" : pct < 80 ? "var(--orange)" : "var(--red)";

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>
          Risk Monitor
        </h1>
        <div className="flex items-center gap-2">
          {risk?.kill_switch_active ? (
            <button
              onClick={deactivateKillSwitch}
              disabled={acting}
              className="flex items-center gap-2 px-4 py-2 rounded text-sm font-semibold disabled:opacity-50"
              style={{ background: "var(--green-dim)", color: "var(--green)", border: "1px solid rgba(0,229,160,0.3)" }}
            >
              <Power size={14} />
              Resume Trading
            </button>
          ) : (
            <button
              onClick={activateKillSwitch}
              disabled={acting}
              className="flex items-center gap-2 px-4 py-2 rounded text-sm font-semibold disabled:opacity-50"
              style={{ background: "var(--red-dim)", color: "var(--red)", border: "1px solid rgba(255,61,87,0.3)" }}
            >
              <AlertOctagon size={14} />
              Kill Switch
            </button>
          )}
        </div>
      </div>

      {/* Kill switch banner */}
      {risk?.kill_switch_active && (
        <div
          className="flex items-center gap-3 px-5 py-4 rounded-lg"
          style={{ background: "var(--red-dim)", border: "1px solid var(--red)" }}
        >
          <AlertOctagon size={18} style={{ color: "var(--red)" }} />
          <div>
            <p className="text-sm font-semibold" style={{ color: "var(--red)" }}>Kill switch active — all trading halted</p>
            <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>Click "Resume Trading" to re-enable execution</p>
          </div>
        </div>
      )}

      {/* Gauges */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[
          { label: "Daily Drawdown", current: risk?.daily_drawdown_pct ?? 0, max: risk?.max_daily_drawdown_pct ?? 3, suffix: "%", decimals: 2, fill: drawdownFill },
          { label: "Consecutive Losses", current: risk?.consecutive_losses ?? 0, max: risk?.max_consecutive_losses ?? 5, suffix: "", decimals: 0, fill: lossBar },
          { label: "Open Positions", current: risk?.open_positions ?? 0, max: risk?.max_open_positions ?? 5, suffix: "", decimals: 0, fill: posBar },
        ].map(g => (
          <div key={g.label} className="panel p-5 space-y-3">
            <div className="flex justify-between text-xs">
              <span style={{ color: "var(--text-secondary)" }}>{g.label}</span>
              <span className="mono font-semibold" style={{ color: "var(--text-primary)" }}>
                {g.current.toFixed(g.decimals)}{g.suffix} / {g.max}{g.suffix}
              </span>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: "var(--bg-surface-3)" }}>
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{ width: `${g.fill}%`, background: barColor(g.fill) }}
              />
            </div>
            <div className="mono text-2xl font-bold" style={{ color: barColor(g.fill) }}>
              {g.current.toFixed(g.decimals)}{g.suffix}
            </div>
          </div>
        ))}
      </div>

      {/* Status panel */}
      <div className="panel p-5 space-y-3">
        <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
          System Status
        </span>
        <div className="grid grid-cols-2 gap-3 text-xs">
          {[
            { label: "Trading Mode", value: risk?.trading_mode?.toUpperCase() ?? "—", badge: risk?.trading_mode === "paper" ? "badge-orange" : risk?.trading_mode === "live" ? "badge-green" : "badge-red" },
            { label: "Kill Switch", value: risk?.kill_switch_active ? "ACTIVE" : "INACTIVE", badge: risk?.kill_switch_active ? "badge-red" : "badge-green" },
            { label: "Cooldown", value: risk?.in_cooldown ? "IN COOLDOWN" : "CLEAR", badge: risk?.in_cooldown ? "badge-orange" : "badge-green" },
            { label: "Cooldown Until", value: risk?.cooldown_until ? new Date(risk.cooldown_until).toLocaleTimeString() : "N/A", badge: "badge-gray" },
          ].map(item => (
            <div key={item.label} className="flex items-center justify-between py-2 border-b" style={{ borderColor: "var(--border-subtle)" }}>
              <span style={{ color: "var(--text-secondary)" }}>{item.label}</span>
              <span className={`badge ${item.badge}`}>{item.value}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
