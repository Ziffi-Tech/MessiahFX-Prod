"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { TrendingUp, TrendingDown, Minus } from "lucide-react";
import { api } from "@/lib/api";
import { useLiveStore, tickKey, type LiveTick } from "@/lib/stores/live";

function displaySymbol(t: LiveTick): string {
  // Oanda uses EUR_USD; show the conventional EUR/USD.
  return t.venue === "oanda" ? t.symbol.replace("_", "/") : t.symbol;
}

function isFx(t: LiveTick): boolean {
  return t.market_type === "forex" || t.venue === "oanda";
}

function fmtPrice(value: number | null, fx: boolean): string {
  if (value == null) return "—";
  const d = fx ? 5 : 2;
  return value.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function dirColor(dir?: number): string {
  if (dir === 1) return "var(--green)";
  if (dir === -1) return "var(--red)";
  return "var(--text-tertiary)";
}

export function PriceGrid() {
  // First-paint snapshot + slow polling fallback in case the SSE stream drops.
  const { data: snapshot } = useQuery({
    queryKey: ["market", "ticks", "latest"],
    queryFn: () => api.market.ticksLatest(),
    refetchInterval: 15_000,
  });

  const liveTicks = useLiveStore((s) => s.ticks);
  const connected = useLiveStore((s) => s.connected);

  // Snapshot defines the symbol set + stable order; live store overlays updates.
  const rows = useMemo<LiveTick[]>(() => {
    const base = snapshot?.ticks ?? [];
    return base.map((t) => liveTicks[tickKey(t.venue, t.symbol)] ?? t);
  }, [snapshot, liveTicks]);

  return (
    <div className="panel">
      <div
        className="flex items-center justify-between px-4 py-3 border-b"
        style={{ borderColor: "var(--border)" }}
      >
        <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
          Market Prices
        </span>
        <div className="flex items-center gap-1.5">
          <span
            className="live-dot"
            style={!connected ? { background: "var(--text-tertiary)" } : undefined}
          />
          <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            {connected ? "LIVE" : "CONNECTING"}
          </span>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="p-6 text-center text-xs" style={{ color: "var(--text-tertiary)" }}>
          Waiting for market-data feed…
        </div>
      ) : (
        <div className="grid grid-cols-2 divide-x divide-y" style={{ borderColor: "var(--border-subtle)" }}>
          {rows.map((t) => {
            const fx = isFx(t);
            const arrow =
              t.dir === 1 ? <TrendingUp size={11} /> :
              t.dir === -1 ? <TrendingDown size={11} /> :
              <Minus size={11} />;
            return (
              <div key={tickKey(t.venue, t.symbol)} className="p-4 space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                    {displaySymbol(t)}
                  </span>
                  <span className="text-[10px] badge badge-gray">{t.venue.toUpperCase()}</span>
                </div>
                <div className="mono text-base font-bold" style={{ color: dirColor(t.dir) }}>
                  {fmtPrice(t.mid ?? t.bid, fx)}
                </div>
                <div className="flex items-center justify-between text-[10px] mono" style={{ color: "var(--text-tertiary)" }}>
                  <span className="flex items-center gap-1" style={{ color: dirColor(t.dir) }}>
                    {arrow}
                    {t.spread_bps != null ? `${t.spread_bps.toFixed(1)} bps` : "—"}
                  </span>
                  <span>
                    {fmtPrice(t.bid, fx)} / {fmtPrice(t.ask, fx)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
