"use client";

import { ToggleLeft, ToggleRight } from "lucide-react";
import { useStrategies, useToggleStrategy } from "@/lib/hooks";
import { StrategyHealth } from "@/components/trading/strategy-health";
import { ParamGovernancePanel } from "@/components/trading/param-governance";
import type { StrategyType } from "@/types/api";

const META: Record<StrategyType, { label: string; description: string; color: string }> = {
  funding_arb: {
    label: "Funding Arbitrage",
    description:
      "Captures funding rate differentials between perp futures and spot markets. Regime-neutral, delta-neutral long spot / short perp.",
    color: "var(--blue)",
  },
  stat_arb: {
    label: "Statistical Arbitrage",
    description:
      "Z-score mean reversion on correlated pairs (spot vs perp spread). Performs best in ranging / mean-reverting regimes.",
    color: "var(--purple)",
  },
  swing: {
    label: "Swing Trading",
    description:
      "TradingView-native signal strategy. Fires on webhook alerts — all direction decisions come from your TradingView setup.",
    color: "var(--orange)",
  },
  breakout: {
    label: "ATR Breakout",
    description:
      "Detects genuine range breakouts filtered by ATR. Targets trending regimes; skips ranging and low-vol to avoid false breakouts.",
    color: "var(--green)",
  },
  mean_reversion_scalp: {
    label: "Mean Reversion Scalp",
    description:
      "RSI + Bollinger Band confluence for short-term mean reversion. Performs best in ranging / low-volatility regimes.",
    color: "var(--yellow)",
  },
  momentum: {
    label: "Momentum Continuation",
    description:
      "Multi-timeframe Rate-of-Change alignment (1-bar, 5-bar, 20-bar). Fires only when all three timeframes confirm direction.",
    color: "var(--red)",
  },
};

export default function StrategiesPage() {
  const { data, isLoading } = useStrategies();
  const toggle = useToggleStrategy();
  const strategies = data?.strategies ?? [];

  return (
    <div className="space-y-5">
      <div>
        <p className="text-xs mt-0.5" style={{ color: "var(--text-secondary)" }}>
          Paper mode active. Enabling a strategy takes effect immediately — no restart required.
        </p>
      </div>

      {/* Strategy cards */}
      {isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4, 5, 6].map((i) => (
            <div key={i} className="panel h-24 animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {strategies.map((s) => {
            const meta = META[s.strategy_type] ?? {
              label: s.strategy_type,
              description: "",
              color: "var(--blue)",
            };
            const isToggling = toggle.variables?.id === s.id && toggle.isPending;

            return (
              <div key={s.id} className="panel p-5">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0 space-y-1.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span
                        className="w-2 h-2 rounded-full shrink-0"
                        style={{
                          background: s.enabled ? meta.color : "var(--text-tertiary)",
                        }}
                      />
                      <span className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                        {meta.label}
                      </span>
                      <span className="badge badge-gray">{s.latency_profile}</span>
                      {s.paper_mode && <span className="badge badge-orange">PAPER</span>}
                    </div>
                    <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                      {meta.description}
                    </p>
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
                    onClick={() =>
                      toggle.mutate({ id: s.id, enabled: !s.enabled })
                    }
                    disabled={isToggling}
                    className="shrink-0 transition-opacity disabled:opacity-50"
                    style={{
                      color: s.enabled ? meta.color : "var(--text-tertiary)",
                    }}
                    aria-label={s.enabled ? `Disable ${meta.label}` : `Enable ${meta.label}`}
                  >
                    {s.enabled ? <ToggleRight size={32} /> : <ToggleLeft size={32} />}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Parameter governance — versioned + audited params */}
      {strategies.length > 0 && (
        <div className="pt-2">
          <ParamGovernancePanel strategies={strategies.map((s) => s.strategy_type)} />
        </div>
      )}

      {/* Strategy health below the toggles */}
      <div className="pt-2">
        <h2 className="text-xs font-semibold mb-3" style={{ color: "var(--text-primary)" }}>
          Operational Health
        </h2>
        <StrategyHealth />
      </div>
    </div>
  );
}
