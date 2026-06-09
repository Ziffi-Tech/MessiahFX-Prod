// ─── SSE client — single EventSource into the gateway real-time spine ───────
// Mounted once (see components/trading/stream-connector.tsx) for the whole app.
// Pumps the live store AND the TanStack Query cache, so existing polled consumers
// (risk gauges, KPI tiles, signal feed) become real-time with no component changes.

"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLiveStore, type LiveTick, type LiveRisk, type LiveSignal } from "./stores/live";
import type { RiskState } from "@/types/api";

const STREAM_URL = "/api/gateway/stream";

// Query keys the stream feeds (kept in sync with lib/hooks.ts QK).
const RISK_STATE_KEY = ["risk", "state"] as const;
const SIGNALS_KEY = ["journal", "signals"] as const;

function num(v: string | undefined, fallback: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/**
 * Merge a live risk frame onto the cached, normalised RiskState. Limits and
 * cooldown (not in the SSE frame) are preserved from the polled query; the live
 * values override. Returns the previous object unchanged when nothing moved, so
 * unchanged frames don't trigger re-renders.
 */
function mergeLiveRisk(prev: RiskState | undefined, live: LiveRisk): RiskState | undefined {
  if (!prev) return prev; // wait for the query to seed limits + structure first
  const rs = live.risk_state ?? {};
  const next: RiskState = {
    ...prev,
    kill_switch_active: live.halted,
    trading_mode: live.halted ? "halted" : prev.trading_mode === "halted" ? "paper" : prev.trading_mode,
    daily_drawdown_pct: num(rs.daily_drawdown_pct, prev.daily_drawdown_pct),
    consecutive_losses: num(rs.consecutive_losses, prev.consecutive_losses),
    open_positions: num(rs.open_position_count, prev.open_positions),
  };
  if (
    next.kill_switch_active === prev.kill_switch_active &&
    next.trading_mode === prev.trading_mode &&
    next.daily_drawdown_pct === prev.daily_drawdown_pct &&
    next.consecutive_losses === prev.consecutive_losses &&
    next.open_positions === prev.open_positions
  ) {
    return prev;
  }
  return next;
}

/**
 * Open the SSE connection and pump events into the live store + query cache.
 * EventSource reconnects automatically; we just reflect connection state.
 */
export function useLiveStream(): void {
  const qc = useQueryClient();

  useEffect(() => {
    const applyTicks = useLiveStore.getState().applyTicks;
    const setRisk = useLiveStore.getState().setRisk;
    const pushSignals = useLiveStore.getState().pushSignals;
    const setConnected = useLiveStore.getState().setConnected;

    const es = new EventSource(STREAM_URL);

    es.addEventListener("open", () => setConnected(true));

    es.addEventListener("ticks", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as { ticks: LiveTick[] };
        applyTicks(data.ticks ?? []);
        setConnected(true);
      } catch { /* ignore malformed frame */ }
    });

    es.addEventListener("risk", (e) => {
      try {
        const live = JSON.parse((e as MessageEvent).data) as LiveRisk;
        setRisk(live);
        // Real-time gauges/KPIs: merge onto the normalised RiskState in the cache.
        qc.setQueryData<RiskState>(RISK_STATE_KEY, (prev) => mergeLiveRisk(prev, live));
      } catch { /* ignore */ }
    });

    es.addEventListener("signals", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as { signals: LiveSignal[] };
        if (data.signals?.length) {
          pushSignals(data.signals);
          // A new opportunity hit the stream — refetch the rich (lifecycle) feed now.
          void qc.invalidateQueries({ queryKey: SIGNALS_KEY });
        }
      } catch { /* ignore */ }
    });

    es.addEventListener("error", () => {
      // EventSource will retry on its own (server sends `retry:`). Mark down.
      setConnected(false);
    });

    return () => es.close();
  }, [qc]);
}
