"use client";

import { useEffect, useState } from "react";
import { Bell, RefreshCw } from "lucide-react";

export function Topbar({ title }: { title?: string }) {
  const [time, setTime] = useState<string>("");
  const [status, setStatus] = useState<"ok" | "checking">("checking");

  useEffect(() => {
    const tick = () =>
      setTime(new Date().toLocaleTimeString("en-GB", { hour12: false }));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    fetch("/api/gateway/health/live", { cache: "no-store" })
      .then((r) => (r.ok ? setStatus("ok") : setStatus("checking")))
      .catch(() => setStatus("checking"));
  }, []);

  return (
    <header
      className="flex items-center justify-between px-6 h-14 border-b shrink-0"
      style={{
        background: "var(--bg-surface)",
        borderColor: "var(--border)",
      }}
    >
      {/* Left: page title */}
      <div className="flex items-center gap-3">
        {title && (
          <h1 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            {title}
          </h1>
        )}
      </div>

      {/* Right: status + time */}
      <div className="flex items-center gap-5">
        {/* System status */}
        <div className="flex items-center gap-2">
          <span className={status === "ok" ? "live-dot" : undefined}
            style={status !== "ok" ? { width: 6, height: 6, borderRadius: "50%", background: "var(--orange)" } : undefined}
          />
          <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
            {status === "ok" ? "Systems online" : "Connecting…"}
          </span>
        </div>

        {/* Clock */}
        <span
          className="mono text-xs"
          style={{ color: "var(--text-secondary)", minWidth: 65 }}
        >
          {time} UTC+1
        </span>

        {/* Notifications (placeholder) */}
        <button
          className="p-1.5 rounded transition-colors hover:bg-[var(--bg-hover)]"
          style={{ color: "var(--text-secondary)" }}
        >
          <Bell size={15} />
        </button>
      </div>
    </header>
  );
}
