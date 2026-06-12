// ─── Workspace layouts — named panel arrangements for the dashboard ─────────
// A workspace is a named set of visible dashboard panels. Built-in presets cover
// the common desks (trading / markets / risk / quant); toggling any panel forks
// into the "custom" workspace. Persisted to localStorage (zustand persist).
//
// Hydration: persist uses skipHydration so the server-rendered defaults match the
// first client render; WorkspaceHydration (mounted in the trading layout) calls
// rehydrate() after mount, then the saved workspace applies.

import { create } from "zustand";
import { persist } from "zustand/middleware";

export const PANELS = [
  { id: "kpis",    label: "KPI Row" },
  { id: "chart",   label: "Price Chart" },
  { id: "risk",    label: "Risk Monitor" },
  { id: "depth",   label: "Order Book" },
  { id: "ai",      label: "AI Digest" },
  { id: "tape",    label: "Market Prices" },
  { id: "health",  label: "Strategy Health" },
  { id: "signals", label: "Recent Signals" },
] as const;

export type PanelId = (typeof PANELS)[number]["id"];

const ALL: PanelId[] = PANELS.map((p) => p.id);

export const WORKSPACE_PRESETS: Record<string, PanelId[]> = {
  trading: ALL,
  markets: ["kpis", "chart", "depth", "tape"],
  risk:    ["kpis", "risk", "health", "signals"],
  quant:   ["kpis", "chart", "health", "signals"],
};

export const WORKSPACE_NAMES = Object.keys(WORKSPACE_PRESETS);

interface WorkspaceStore {
  active: string;                 // preset name or "custom"
  custom: PanelId[];              // panel set for the custom workspace
  visible: (id: PanelId) => boolean;
  setWorkspace: (name: string) => void;
  togglePanel: (id: PanelId) => void;
}

export const useWorkspaceStore = create<WorkspaceStore>()(
  persist(
    (set, get) => ({
      active: "trading",
      custom: ALL,

      visible: (id) => {
        const { active, custom } = get();
        const panels = active === "custom" ? custom : WORKSPACE_PRESETS[active] ?? ALL;
        return panels.includes(id);
      },

      setWorkspace: (name) =>
        set({ active: name === "custom" || WORKSPACE_PRESETS[name] ? name : "trading" }),

      togglePanel: (id) =>
        set((s) => {
          const base = s.active === "custom" ? s.custom : WORKSPACE_PRESETS[s.active] ?? ALL;
          const next = base.includes(id) ? base.filter((p) => p !== id) : [...base, id];
          return { active: "custom", custom: next };
        }),
    }),
    {
      name: "mezna-workspace",
      skipHydration: true,   // hydrate after mount (WorkspaceHydration) — no SSR mismatch
    },
  ),
);
