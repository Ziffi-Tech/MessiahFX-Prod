"use client";

import { Play, Square, AlertOctagon, Wifi, WifiOff } from "lucide-react";
import { useRiskState, useKillSwitch, useBotStart, useBotStop } from "@/lib/hooks";
import { useLiveStore } from "@/lib/stores/live";

/**
 * Global bot lifecycle controls + real-time stream health, always reachable
 * from the topbar. START clears the halt and enables all strategies (paper);
 * STOP halts and disables them; KILL is the emergency immediate halt.
 */
export function BotControls() {
  const { data: risk } = useRiskState();
  const liveRisk = useLiveStore((s) => s.risk);
  const connected = useLiveStore((s) => s.connected);
  const ks = useKillSwitch();
  const start = useBotStart();
  const stop = useBotStop();

  // Prefer the SSE halt flag (sub-second) and fall back to the polled risk state.
  const halted = liveRisk?.halted ?? risk?.kill_switch_active ?? false;
  const mode = halted ? "HALTED" : risk?.trading_mode === "live" ? "LIVE" : "PAPER";
  const busy = ks.isPending || start.isPending || stop.isPending;

  const onStart = () => start.mutate(true); // paper mode
  const onStop = () => {
    if (confirm("Stop the bot? This halts trading and disables all strategies.")) {
      stop.mutate("Manual stop from dashboard");
    }
  };
  const onKill = () => {
    if (confirm("EMERGENCY KILL — halt ALL trading immediately?")) ks.mutate(true);
  };

  return (
    <div className="flex items-center gap-3">
      {/* Real-time stream health */}
      <div
        className="flex items-center gap-1.5"
        title={connected ? "Real-time stream connected" : "Stream reconnecting…"}
      >
        {connected ? (
          <Wifi size={13} style={{ color: "var(--green)" }} />
        ) : (
          <WifiOff size={13} style={{ color: "var(--orange)" }} />
        )}
        <span className="text-[10px] mono" style={{ color: "var(--text-tertiary)" }}>
          {connected ? "STREAM" : "RECONNECT"}
        </span>
      </div>

      {/* Trading mode */}
      <span className={`badge ${halted ? "badge-red" : mode === "LIVE" ? "badge-green" : "badge-orange"}`}>
        {mode}
      </span>

      {/* Start / Stop */}
      {halted ? (
        <button
          onClick={onStart}
          disabled={busy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold disabled:opacity-50"
          style={{ background: "var(--green-dim)", color: "var(--green)", border: "1px solid rgba(0,229,160,0.3)" }}
        >
          <Play size={12} />
          {start.isPending ? "Starting…" : "Start"}
        </button>
      ) : (
        <button
          onClick={onStop}
          disabled={busy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold disabled:opacity-50"
          style={{ background: "var(--orange-dim)", color: "var(--orange)", border: "1px solid rgba(255,170,0,0.3)" }}
        >
          <Square size={12} />
          {stop.isPending ? "Stopping…" : "Stop"}
        </button>
      )}

      {/* Emergency kill — disabled when already halted */}
      <button
        onClick={onKill}
        disabled={busy || halted}
        title="Emergency kill switch"
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-xs font-semibold disabled:opacity-40"
        style={{ background: "var(--red-dim)", color: "var(--red)", border: "1px solid rgba(255,61,87,0.3)" }}
      >
        <AlertOctagon size={12} />
        Kill
      </button>
    </div>
  );
}
