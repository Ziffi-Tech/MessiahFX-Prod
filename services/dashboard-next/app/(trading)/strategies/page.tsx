"use client";

import { useEffect, useState } from "react";
import type { StrategyConfig } from "@/types/api";
import { Settings2, ToggleLeft, ToggleRight } from "lucide-react";

const STRATEGY_META: Record<string, { label: string; description: string; color: string }> = {
  funding_arb: {
    label: "Funding Arbitrage",
    description: "Exploits funding rate differentials between perpetual futures and spot markets. Runs on relaxed latency.",
    color: "var(--blue)",
  },
  stat_arb: {
    label: "Statistical Arbitrage",
    description: "Z-score based mean reversion on correlated cryptocurrency pairs with dynamic entry/exit thresholds.",
    color: "var(--purple)",
  },
  swing: {
    label: "Swing Trading",
    description: "Multi-timeframe momentum strategy combining TradingView signals with AI-enhanced entry scoring.",
    color: "var(--orange)",
  },
};

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<StrategyConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);

  const load = async () => {
    try {
      const res = await fetch("/api/gateway/strategy/configs", { cache: "no-store" });
      if (res.ok) {
        const json = await res.json();
        setStrategies(json.strategies ?? []);
      }
    } catch { /* */ } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const toggle = async (id: string, current: boolean) => {
    setToggling(id);
    try {
      const res = await fetch(`/api/gateway/strategy/configs/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !current }),
      });
      if (res.ok) await load();
    } catch { /* */ } finally {
      setToggling(null);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-base font-bold" style={{ color: "var(--text-primary)" }}>
          Strategy Controls
        </h1>
        <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
          All strategies run in paper mode. Enabling a strategy starts it immediately.
        </p>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="panel h-28 animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {strategies.map(s => {
            const meta = STRATEGY_META[s.strategy_type] ?? { label: s.strategy_type, description: "", color: "var(--blue)" };
            return (
              <div key={s.id} className="panel p-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0 space-y-1.5">
                    <div className="flex items-center gap-2">
                      <span
                        className="w-2 h-2 rounded-full"
                        style={{ background: s.enabled ? meta.color : "var(--text-tertiary)" }}
                      />
                      <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                        {meta.label}
                      </span>
                      <span className="badge badge-gray">{s.latency_profile}</span>
                      {s.paper_mode && <span className="badge badge-orange">PAPER</span>}
                    </div>
                    <p className="text-xs" style={{ color: "var(--text-secondary)" }}>
                      {meta.description}
                    </p>

                    {/* Params preview */}
                    {Object.keys(s.params).length > 0 && (
                      <div className="flex flex-wrap gap-2 pt-1">
                        {Object.entries(s.params).map(([k, v]) => (
                          <span
                            key={k}
                            className="text-[10px] mono px-2 py-0.5 rounded"
                            style={{
                              background: "var(--bg-surface-2)",
                              color: "var(--text-secondary)",
                              border: "1px solid var(--border-subtle)",
                            }}
                          >
                            {k}: {String(v)}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Toggle */}
                  <button
                    onClick={() => toggle(s.id, s.enabled)}
                    disabled={toggling === s.id}
                    className="shrink-0 transition-opacity disabled:opacity-50"
                    style={{ color: s.enabled ? meta.color : "var(--text-tertiary)" }}
                    aria-label={s.enabled ? "Disable strategy" : "Enable strategy"}
                  >
                    {s.enabled ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
