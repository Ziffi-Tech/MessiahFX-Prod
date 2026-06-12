"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Activity, Play } from "lucide-react";
import { api } from "@/lib/api";

const inputStyle: React.CSSProperties = {
  background: "var(--bg-surface-2)", border: "1px solid var(--border)", color: "var(--text-primary)",
};

function fmt(n: number | null | undefined, dp = 2): string {
  return n == null ? "—" : n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function VolatilityPanel() {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [interval, setInterval] = useState("1h");
  const [method, setMethod] = useState<"ewma" | "garch">("garch");
  const [targetVol, setTargetVol] = useState(0.5);

  const run = useMutation({
    mutationFn: () => api.backtest.volatility({ symbol, interval, method, days: 30, target_vol: targetVol }),
  });
  const r = run.data;

  return (
    <div className="panel">
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-2">
          <Activity size={14} style={{ color: "var(--green)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Volatility Forecast &amp; Sizing</span>
        </div>
        {r?.status === "ok" && <span className="badge badge-gray">{r.method?.toUpperCase()}</span>}
      </div>

      <div className="p-4 space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
            <span>Symbol</span>
            <input value={symbol} onChange={(e) => setSymbol(e.target.value)} className="block mono text-xs px-2 py-1.5 rounded w-32" style={inputStyle} />
          </label>
          <label className="text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
            <span>Interval</span>
            <input value={interval} onChange={(e) => setInterval(e.target.value)} className="block mono text-xs px-2 py-1.5 rounded w-20" style={inputStyle} />
          </label>
          <label className="text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
            <span>Method</span>
            <select value={method} onChange={(e) => setMethod(e.target.value as "ewma" | "garch")} className="block mono text-xs px-2 py-1.5 rounded w-24" style={inputStyle}>
              <option value="garch">GARCH</option>
              <option value="ewma">EWMA</option>
            </select>
          </label>
          <label className="text-[11px] space-y-1" style={{ color: "var(--text-secondary)" }}>
            <span>Target vol (ann.)</span>
            <input type="number" step="0.05" value={targetVol} onChange={(e) => setTargetVol(Number(e.target.value))} className="block mono text-xs px-2 py-1.5 rounded w-24" style={inputStyle} />
          </label>
          <button
            type="button"
            onClick={() => run.mutate()}
            disabled={run.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold disabled:opacity-50"
            style={{ background: "var(--blue)", color: "#fff" }}
          >
            <Play size={12} />
            {run.isPending ? "…" : "Forecast"}
          </button>
        </div>

        {run.isError && <p className="text-xs" style={{ color: "var(--red)" }}>Request failed.</p>}
        {r?.status === "insufficient_data" && (
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            Not enough OHLCV ({r.candles} candles / {r.returns} returns). {r.detail}
          </p>
        )}

        {r?.status === "ok" && (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-xs">
            <Stat label="Forecast vol (annualised)" value={`${fmt((r.forecast_vol_annualized ?? 0) * 100, 1)}%`} />
            <Stat label="Sizing multiplier" value={`${fmt(r.sizing_multiplier, 2)}×`} color={(r.sizing_multiplier ?? 1) < 1 ? "var(--orange)" : "var(--green)"} />
            <Stat label="Returns" value={String(r.returns)} />
            {r.garch_params && (
              <>
                <Stat label="GARCH α" value={fmt(r.garch_params.alpha, 3)} />
                <Stat label="GARCH β" value={fmt(r.garch_params.beta, 3)} />
                <Stat label="Persistence α+β" value={fmt(r.garch_params.alpha + r.garch_params.beta, 3)} />
              </>
            )}
          </div>
        )}
        <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
          Sizing multiplier = target ÷ forecast vol (clamped). The executor uses a unit-free relative
          multiplier (long-run ÷ recent vol) for live sizing when VOL_TARGET_ENABLED.
        </p>
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded p-2.5" style={{ background: "var(--bg-surface-2)" }}>
      <div className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{label}</div>
      <div className="mono text-sm font-bold" style={{ color: color ?? "var(--text-primary)" }}>{value}</div>
    </div>
  );
}
