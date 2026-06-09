// ─── Typed TanStack Query hooks — single source of truth for all API state ──

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

// ── Query key registry ────────────────────────────────────────────────────────
export const QK = {
  health:           ["health"]             as const,
  riskState:        ["risk", "state"]      as const,
  pnl:              ["journal", "pnl"]     as const,
  trades:    (p?:object) => ["journal", "trades", p] as const,
  signals:   (n:number) => ["journal", "signals", n] as const,
  strategies:       ["strategy", "configs"] as const,
  overview:         ["strategy", "overview"] as const,
  regime:           ["ai", "regime"]       as const,
  digest:           ["ai", "digest"]       as const,
} as const;

// ── Health ────────────────────────────────────────────────────────────────────
export function useHealth() {
  return useQuery({
    queryKey: QK.health,
    queryFn: () => api.health.live(),
    refetchInterval: 10_000,
  });
}

// ── Risk state ────────────────────────────────────────────────────────────────
export function useRiskState() {
  return useQuery({
    queryKey: QK.riskState,
    queryFn: () => api.risk.state(),
    refetchInterval: 5_000,
  });
}

// ── P&L summary ───────────────────────────────────────────────────────────────
export function usePnL() {
  return useQuery({
    queryKey: QK.pnl,
    queryFn: () => api.journal.pnl(),
    refetchInterval: 15_000,
  });
}

// ── Trade list ────────────────────────────────────────────────────────────────
export function useTrades(params?: { limit?: number; status?: string }) {
  return useQuery({
    queryKey: QK.trades(params),
    queryFn: () => api.journal.trades(params),
    refetchInterval: 10_000,
  });
}

// ── Recent signals (opportunities) ────────────────────────────────────────────
export function useSignals(limit = 15) {
  return useQuery({
    queryKey: QK.signals(limit),
    queryFn: () => api.journal.opportunities(limit),
    refetchInterval: 15_000,
  });
}

// ── Strategy configs ──────────────────────────────────────────────────────────
export function useStrategies() {
  return useQuery({
    queryKey: QK.strategies,
    queryFn: () => api.strategy.list(),
    refetchInterval: 30_000,
  });
}

// ── Strategy operational overview ─────────────────────────────────────────────
export function useStrategyOverview() {
  return useQuery({
    queryKey: QK.overview,
    queryFn: () => api.strategy.overview(),
    refetchInterval: 15_000,
  });
}

// ── Market regime ─────────────────────────────────────────────────────────────
export function useRegime() {
  return useQuery({
    queryKey: QK.regime,
    queryFn: () => api.aiFilter.regimeCached(),
    refetchInterval: 60_000,
    // 404 = no cached regime yet — return null gracefully
    retry: false,
  });
}

// ── AI digest ─────────────────────────────────────────────────────────────────
export function useAiDigest() {
  return useQuery({
    queryKey: QK.digest,
    queryFn: () => api.aiFilter.digest(),
    refetchInterval: 5 * 60_000,  // Digest is expensive — 5-min poll
  });
}

// ── Kill switch mutation ──────────────────────────────────────────────────────
export function useKillSwitch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (activate: boolean) => api.risk.killSwitch(activate),
    onSuccess: () => {
      // Immediately invalidate so all risk-dependent UIs update
      void qc.invalidateQueries({ queryKey: QK.riskState });
    },
  });
}

// ── Strategy toggle mutation ──────────────────────────────────────────────────
export function useToggleStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      api.strategy.toggle(id, enabled),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QK.strategies });
    },
  });
}
