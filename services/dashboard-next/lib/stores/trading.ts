import { create } from "zustand";
import type { Trade, RiskState, Opportunity } from "@/types/api";

interface TradingStore {
  // Live state
  trades: Trade[];
  riskState: RiskState | null;
  recentSignals: Opportunity[];
  regime: string | null;
  tradingMode: "paper" | "live" | "halted";

  // Actions
  setTrades: (trades: Trade[]) => void;
  setRiskState: (state: RiskState) => void;
  setRecentSignals: (signals: Opportunity[]) => void;
  setRegime: (regime: string) => void;
  setTradingMode: (mode: "paper" | "live" | "halted") => void;
}

export const useTradingStore = create<TradingStore>((set) => ({
  trades: [],
  riskState: null,
  recentSignals: [],
  regime: null,
  tradingMode: "paper",

  setTrades: (trades) => set({ trades }),
  setRiskState: (riskState) => set({ riskState, tradingMode: riskState.trading_mode }),
  setRecentSignals: (recentSignals) => set({ recentSignals }),
  setRegime: (regime) => set({ regime }),
  setTradingMode: (tradingMode) => set({ tradingMode }),
}));
