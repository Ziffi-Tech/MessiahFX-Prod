"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Bell, Search } from "lucide-react";
import { BotControls } from "@/components/layout/bot-controls";

const ROUTE_TITLES: Record<string, string> = {
  "/":           "Live Dashboard",
  "/positions":  "Positions & P&L",
  "/strategies": "Strategy Controls",
  "/backtest":   "Backtest & Optimiser",
  "/performance": "Performance & TCA",
  "/risk":       "Risk Monitor",
  "/journal":    "Trade Journal",
  "/rag":        "RAG Studio",
  "/settings":   "Settings",
};

function Clock() {
  const [time, setTime] = useState("");
  useEffect(() => {
    const tick = () =>
      setTime(
        new Date().toLocaleTimeString("en-GB", {
          hour12: false,
          timeZone: "UTC",
        }) + " UTC"
      );
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="mono text-xs" style={{ color: "var(--text-secondary)", minWidth: 75 }}>
      {time}
    </span>
  );
}

export function Topbar() {
  const path = usePathname();

  // Match longest prefix first so /backtest matches before /
  const title =
    Object.entries(ROUTE_TITLES)
      .sort((a, b) => b[0].length - a[0].length)
      .find(([route]) => (route === "/" ? path === "/" : path.startsWith(route)))?.[1] ??
    "MeznaQuantFX";

  return (
    <header
      className="flex items-center justify-between px-6 h-14 border-b shrink-0"
      style={{ background: "var(--bg-surface)", borderColor: "var(--border)" }}
    >
      {/* Page title */}
      <h1 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
        {title}
      </h1>

      {/* Right cluster */}
      <div className="flex items-center gap-5">
        {/* Command palette affordance */}
        <button
          type="button"
          onClick={() => window.dispatchEvent(new Event("mezna:command-palette"))}
          className="hidden md:flex items-center gap-1.5 px-2 py-1 rounded text-[11px]"
          style={{ background: "var(--bg-surface-2)", color: "var(--text-tertiary)", border: "1px solid var(--border)" }}
          title="Command palette"
        >
          <Search size={12} />
          <kbd className="mono">⌘K</kbd>
        </button>

        {/* Bot lifecycle + real-time stream health */}
        <BotControls />

        <Clock />

        <button
          aria-label="Notifications"
          className="p-1.5 rounded transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-secondary)" }}
        >
          <Bell size={15} />
        </button>
      </div>
    </header>
  );
}
