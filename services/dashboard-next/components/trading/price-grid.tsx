"use client";

import { useEffect, useState } from "react";
import { TrendingUp, TrendingDown } from "lucide-react";

// Simulated price ticks — real data will come from market-data service
// via WebSocket once the gateway WS endpoint is added
const SYMBOLS = [
  { symbol: "BTC/USDT",  venue: "binance", price: 67420.50, change: 1.24 },
  { symbol: "ETH/USDT",  venue: "binance", price: 3842.10, change: -0.88 },
  { symbol: "EUR/USD",   venue: "oanda",   price: 1.08234, change: 0.12 },
  { symbol: "GBP/USD",   venue: "oanda",   price: 1.27105, change: -0.05 },
];

interface Tick {
  symbol: string;
  venue: string;
  price: number;
  change: number;
}

export function PriceGrid() {
  const [ticks, setTicks] = useState<Tick[]>(SYMBOLS);

  // Simulate price movement until WS is wired up
  useEffect(() => {
    const id = setInterval(() => {
      setTicks((prev) =>
        prev.map((t) => ({
          ...t,
          price: t.price * (1 + (Math.random() - 0.5) * 0.001),
        }))
      );
    }, 2000);
    return () => clearInterval(id);
  }, []);

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
          <span className="live-dot" />
          <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>LIVE</span>
        </div>
      </div>
      <div className="grid grid-cols-2 divide-x divide-y" style={{ borderColor: "var(--border-subtle)" }}>
        {ticks.map((t) => (
          <div key={t.symbol} className="p-4 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
                {t.symbol}
              </span>
              <span className="text-[10px] badge badge-gray">{t.venue.toUpperCase()}</span>
            </div>
            <div className="mono text-base font-bold" style={{ color: "var(--text-primary)" }}>
              {t.price.toLocaleString("en-US", {
                minimumFractionDigits: t.symbol.includes("USD/") || t.symbol.includes("/USD") ? 5 : 2,
                maximumFractionDigits: t.symbol.includes("USD/") || t.symbol.includes("/USD") ? 5 : 2,
              })}
            </div>
            <div
              className="flex items-center gap-1 text-xs mono"
              style={{ color: t.change >= 0 ? "var(--green)" : "var(--red)" }}
            >
              {t.change >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
              {t.change >= 0 ? "+" : ""}{t.change.toFixed(2)}%
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
