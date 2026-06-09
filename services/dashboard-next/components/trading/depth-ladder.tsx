"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useLiveStore, tickKey, type LiveTick } from "@/lib/stores/live";

const LEVELS = 10;

function displaySymbol(t: LiveTick): string {
  return t.venue === "oanda" ? t.symbol.replace("_", "/") : t.symbol;
}

interface Row { price: number; size: number; cum: number }

function ladder(levels: [number, number][]): { rows: Row[]; maxCum: number } {
  const rows: Row[] = [];
  let cum = 0;
  for (const [price, size] of levels.slice(0, LEVELS)) {
    cum += size;
    rows.push({ price, size, cum });
  }
  return { rows, maxCum: cum || 1 };
}

export function DepthLadder() {
  const { data: snapshot } = useQuery({
    queryKey: ["market", "ticks", "latest"],
    queryFn: () => api.market.ticksLatest(),
    refetchInterval: 30_000,
  });
  const symbols = snapshot?.ticks ?? [];

  const [selected, setSelected] = useState<string | null>(null);
  const active = useMemo(() => {
    if (selected) return symbols.find((t) => tickKey(t.venue, t.symbol) === selected) ?? symbols[0];
    return symbols[0];
  }, [selected, symbols]);

  const { data: book, isError, isLoading } = useQuery({
    queryKey: ["orderbook", active?.venue, active?.symbol],
    queryFn: () => api.market.orderbook(active!.venue, active!.symbol),
    enabled: !!active,
    refetchInterval: 1_000,
    retry: false,
  });

  const fx = active?.market_type === "forex" || active?.venue === "oanda";
  const dp = fx ? 5 : 2;

  const asks = book?.asks ? ladder(book.asks) : null;
  const bids = book?.bids ? ladder(book.bids) : null;
  const bestAsk = asks?.rows[0]?.price;
  const bestBid = bids?.rows[0]?.price;
  const spread = bestAsk != null && bestBid != null ? bestAsk - bestBid : null;
  const mid = bestAsk != null && bestBid != null ? (bestAsk + bestBid) / 2 : null;

  return (
    <div className="panel">
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
        <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Order Book</span>
        <select
          value={active ? tickKey(active.venue, active.symbol) : ""}
          onChange={(e) => setSelected(e.target.value)}
          className="mono text-[10px] px-1.5 py-1 rounded outline-none"
          style={{ background: "var(--bg-surface-2)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
        >
          {symbols.map((t) => (
            <option key={tickKey(t.venue, t.symbol)} value={tickKey(t.venue, t.symbol)}>
              {displaySymbol(t)}
            </option>
          ))}
        </select>
      </div>

      {isError || (!isLoading && !book?.bids?.length) ? (
        <div className="px-4 py-8 text-center text-[11px]" style={{ color: "var(--text-tertiary)" }}>
          No L2 book for this symbol.<br />Enable it via <code className="mono">ORDERBOOK_SYMBOLS</code>.
        </div>
      ) : (
        <div className="px-2 py-2 text-[11px] mono">
          {/* Asks — best ask nearest the spread (rendered bottom-up) */}
          <div className="flex flex-col-reverse">
            {asks?.rows.map((r, i) => (
              <Level key={`a${i}`} row={r} maxCum={asks.maxCum} dp={dp} side="ask" />
            ))}
          </div>

          {/* Spread / mid */}
          <div className="flex items-center justify-between px-2 py-1.5 my-0.5"
               style={{ borderTop: "1px solid var(--border-subtle)", borderBottom: "1px solid var(--border-subtle)" }}>
            <span style={{ color: "var(--text-primary)" }}>{mid != null ? mid.toFixed(dp) : "—"}</span>
            <span style={{ color: "var(--text-tertiary)" }}>
              {spread != null && mid ? `${((spread / mid) * 10_000).toFixed(1)} bps` : "—"}
            </span>
          </div>

          {/* Bids */}
          <div className="flex flex-col">
            {bids?.rows.map((r, i) => (
              <Level key={`b${i}`} row={r} maxCum={bids.maxCum} dp={dp} side="bid" />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Level({ row, maxCum, dp, side }: { row: Row; maxCum: number; dp: number; side: "bid" | "ask" }) {
  const color = side === "bid" ? "var(--green)" : "var(--red)";
  const bg = side === "bid" ? "var(--green-dim)" : "var(--red-dim)";
  const width = `${Math.min((row.cum / maxCum) * 100, 100)}%`;
  return (
    <div className="relative flex items-center justify-between px-2 py-[3px]">
      <div className="absolute inset-y-0 right-0" style={{ width, background: bg }} />
      <span className="relative" style={{ color }}>{row.price.toFixed(dp)}</span>
      <span className="relative" style={{ color: "var(--text-secondary)" }}>{row.size.toLocaleString("en-US", { maximumFractionDigits: 4 })}</span>
    </div>
  );
}
