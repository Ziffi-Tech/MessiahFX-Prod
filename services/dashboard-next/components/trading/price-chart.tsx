"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  createChart, CandlestickSeries, ColorType, CrosshairMode,
  type IChartApi, type ISeriesApi, type UTCTimestamp, type CandlestickData,
} from "lightweight-charts";
import { api } from "@/lib/api";
import { useLiveStore, tickKey, type LiveTick } from "@/lib/stores/live";

const INTERVALS = ["1m", "5m", "15m", "1h"] as const;

function displaySymbol(t: LiveTick): string {
  return t.venue === "oanda" ? t.symbol.replace("_", "/") : t.symbol;
}

export function PriceChart() {
  // Symbol universe comes from the live snapshot (configured feed symbols).
  const { data: snapshot } = useQuery({
    queryKey: ["market", "ticks", "latest"],
    queryFn: () => api.market.ticksLatest(),
    refetchInterval: 30_000,
  });
  const symbols = snapshot?.ticks ?? [];

  const [selected, setSelected] = useState<string | null>(null);
  const [interval, setInterval] = useState<(typeof INTERVALS)[number]>("1m");

  // Default to the first symbol once the snapshot lands.
  const active = useMemo(() => {
    if (selected) return symbols.find((t) => tickKey(t.venue, t.symbol) === selected) ?? symbols[0];
    return symbols[0];
  }, [selected, symbols]);

  const { data: ohlcv, isLoading, isError } = useQuery({
    queryKey: ["ohlcv", active?.venue, active?.symbol, interval],
    queryFn: () => api.market.ohlcv({ venue: active!.venue, symbol: active!.symbol, interval, days: 7 }),
    enabled: !!active,
    refetchInterval: 60_000,
  });

  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  // Build the chart once.
  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8b93a7",
        fontFamily: "ui-monospace, monospace",
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.04)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
      timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, secondsVisible: false },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#00e5a0", downColor: "#ff3d57", borderVisible: false,
      wickUpColor: "#00e5a0", wickDownColor: "#ff3d57",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Push history into the series whenever the candle set changes.
  useEffect(() => {
    if (!seriesRef.current || !ohlcv?.candles) return;
    const data: CandlestickData[] = ohlcv.candles.map((c) => ({
      time: Math.floor(c.ts / 1000) as UTCTimestamp,
      open: c.open, high: c.high, low: c.low, close: c.close,
    }));
    seriesRef.current.setData(data);
    chartRef.current?.timeScale().fitContent();
  }, [ohlcv]);

  // Live overlay: nudge the forming (last) bar with the SSE mid for this symbol.
  const liveTick = useLiveStore((s) => (active ? s.ticks[tickKey(active.venue, active.symbol)] : undefined));
  useEffect(() => {
    if (!seriesRef.current || !ohlcv?.candles?.length || !liveTick?.mid) return;
    const last = ohlcv.candles[ohlcv.candles.length - 1];
    seriesRef.current.update({
      time: Math.floor(last.ts / 1000) as UTCTimestamp,
      open: last.open,
      high: Math.max(last.high, liveTick.mid),
      low: Math.min(last.low, liveTick.mid),
      close: liveTick.mid,
    });
  }, [liveTick?.mid, ohlcv]);

  const lastClose = ohlcv?.candles?.at(-1)?.close ?? liveTick?.mid ?? null;

  return (
    <div className="panel flex flex-col" style={{ minHeight: 420 }}>
      {/* Header: symbol selector + interval + last price */}
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-2">
          <select
            value={active ? tickKey(active.venue, active.symbol) : ""}
            onChange={(e) => setSelected(e.target.value)}
            className="mono text-xs px-2 py-1 rounded outline-none"
            style={{ background: "var(--bg-surface-2)", color: "var(--text-primary)", border: "1px solid var(--border)" }}
          >
            {symbols.map((t) => (
              <option key={tickKey(t.venue, t.symbol)} value={tickKey(t.venue, t.symbol)}>
                {displaySymbol(t)} · {t.venue.toUpperCase()}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-0.5">
            {INTERVALS.map((iv) => (
              <button
                key={iv}
                onClick={() => setInterval(iv)}
                className="text-[10px] mono px-1.5 py-1 rounded"
                style={
                  iv === interval
                    ? { background: "var(--blue-dim)", color: "var(--blue)" }
                    : { color: "var(--text-tertiary)" }
                }
              >
                {iv}
              </button>
            ))}
          </div>
        </div>
        {lastClose != null && (
          <span className="mono text-sm font-bold" style={{ color: "var(--text-primary)" }}>
            {lastClose.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 5 })}
          </span>
        )}
      </div>

      {/* Chart canvas */}
      <div className="relative flex-1">
        <div ref={containerRef} className="absolute inset-0" />
        {(isLoading || isError || !ohlcv?.candles?.length) && (
          <div className="absolute inset-0 flex items-center justify-center text-xs" style={{ color: "var(--text-tertiary)" }}>
            {isLoading
              ? "Loading candles…"
              : isError
              ? "No persisted OHLCV service reachable"
              : "No candles yet — run a backfill or wait for the live bar writer"}
          </div>
        )}
      </div>
    </div>
  );
}
