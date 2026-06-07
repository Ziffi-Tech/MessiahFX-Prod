"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Bell, WifiOff } from "lucide-react";
import { useHealth } from "@/lib/hooks";

const ROUTE_TITLES: Record<string, string> = {
  "/":           "Live Dashboard",
  "/positions":  "Positions & P&L",
  "/strategies": "Strategy Controls",
  "/backtest":   "Backtest & Optimiser",
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
  const { data: health, isError } = useHealth();

  // Match longest prefix first so /backtest matches before /
  const title =
    Object.entries(ROUTE_TITLES)
      .sort((a, b) => b[0].length - a[0].length)
      .find(([route]) => (route === "/" ? path === "/" : path.startsWith(route)))?.[1] ??
    "MeznaQuantFX";

  const online = !isError && health?.status === "ok";

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
        {/* System status */}
        <div className="flex items-center gap-1.5">
          {online ? (
            <>
              <span className="live-dot" />
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                Systems online
              </span>
            </>
          ) : (
            <>
              <WifiOff size={13} style={{ color: "var(--orange)" }} />
              <span className="text-xs" style={{ color: "var(--orange)" }}>
                Connecting…
              </span>
            </>
          )}
        </div>

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
