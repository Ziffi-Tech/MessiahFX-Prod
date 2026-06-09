// ─── SSE client — single EventSource into the gateway real-time spine ───────
// Mounted once (see components/trading/stream-connector.tsx) for the whole app.
// Drives the live store: price ticks, risk state, and the opportunity feed.

"use client";

import { useEffect } from "react";
import { useLiveStore, type LiveTick, type LiveRisk, type LiveSignal } from "./stores/live";

const STREAM_URL = "/api/gateway/stream";

/**
 * Open the SSE connection and pump events into the live store.
 * EventSource reconnects automatically; we just reflect connection state.
 * Returns nothing — consume the data via useLiveStore selectors.
 */
export function useLiveStream(): void {
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
        setRisk(JSON.parse((e as MessageEvent).data) as LiveRisk);
      } catch { /* ignore */ }
    });

    es.addEventListener("signals", (e) => {
      try {
        const data = JSON.parse((e as MessageEvent).data) as { signals: LiveSignal[] };
        if (data.signals?.length) pushSignals(data.signals);
      } catch { /* ignore */ }
    });

    es.addEventListener("error", () => {
      // EventSource will retry on its own (server sends `retry:`). Mark down.
      setConnected(false);
    });

    return () => es.close();
  }, []);
}
