// ─── Live market state — fed by the gateway SSE stream (lib/stream.ts) ──────
// High-frequency tick/risk/signal updates live here, isolated from the
// TanStack-Query cache so a price tick never re-renders a backtest panel.

import { create } from "zustand";

export interface LiveTick {
  venue: string;
  symbol: string;
  market_type: string | null;
  bid: number | null;
  ask: number | null;
  mid: number | null;
  spread_bps: number | null;
  timestamp: string | null;
  live?: boolean;
  /** Tick direction vs the previous mid: 1 up, -1 down, 0 unchanged. */
  dir?: number;
}

export interface LiveRisk {
  halted: boolean;
  risk_state: Record<string, string>;
}

export interface LiveSignal {
  _id?: string;
  opportunity_id?: string;
  strategy_type?: string;
  venue?: string;
  symbol_primary?: string;
  net_edge_bps?: string;
  ai_score?: string;
  detected_at?: string;
  payload?: unknown;
  [k: string]: unknown;
}

export function tickKey(venue: string, symbol: string): string {
  return `${venue}:${symbol}`;
}

const MAX_SIGNALS = 50;

interface LiveStore {
  ticks: Record<string, LiveTick>;
  risk: LiveRisk | null;
  signals: LiveSignal[];
  connected: boolean;
  lastEventAt: number | null;

  applyTicks: (ticks: LiveTick[]) => void;
  setRisk: (risk: LiveRisk) => void;
  pushSignals: (signals: LiveSignal[]) => void;
  setConnected: (connected: boolean) => void;
}

export const useLiveStore = create<LiveStore>((set) => ({
  ticks: {},
  risk: null,
  signals: [],
  connected: false,
  lastEventAt: null,

  applyTicks: (incoming) =>
    set((state) => {
      if (!incoming.length) return { lastEventAt: Date.now() };
      const ticks = { ...state.ticks };
      for (const t of incoming) {
        const key = tickKey(t.venue, t.symbol);
        const prev = ticks[key];
        let dir = prev?.dir ?? 0;
        if (prev?.mid != null && t.mid != null) {
          if (t.mid > prev.mid) dir = 1;
          else if (t.mid < prev.mid) dir = -1;
        }
        ticks[key] = { ...t, dir };
      }
      return { ticks, lastEventAt: Date.now() };
    }),

  setRisk: (risk) => set({ risk, lastEventAt: Date.now() }),

  pushSignals: (incoming) =>
    set((state) => ({
      signals: [...incoming.reverse(), ...state.signals].slice(0, MAX_SIGNALS),
      lastEventAt: Date.now(),
    })),

  setConnected: (connected) => set({ connected }),
}));
