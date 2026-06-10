"use client";

import { useEffect } from "react";
import { AlertTriangle, RotateCcw, RefreshCw } from "lucide-react";

// Route-segment error boundary for the trading cockpit. A render error in any
// page now shows this fallback (with recovery) instead of a blank screen.
export default function TradingError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("trading.route_error", error);
  }, [error]);

  return (
    <div className="flex items-center justify-center min-h-[60vh] p-6">
      <div className="panel p-6 space-y-4 max-w-lg w-full">
        <div className="flex items-center gap-2">
          <AlertTriangle size={16} style={{ color: "var(--red)" }} />
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            Something went wrong
          </h2>
        </div>
        <p className="text-xs mono p-3 rounded overflow-x-auto" style={{ background: "var(--bg-surface-2)", color: "var(--text-secondary)" }}>
          {error.message || "An unexpected error occurred."}
          {error.digest ? `\n\ndigest: ${error.digest}` : ""}
        </p>
        <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          The rest of the cockpit is unaffected — your data and the trading engine keep running.
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={reset}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold"
            style={{ background: "var(--blue)", color: "#fff" }}
          >
            <RotateCcw size={12} /> Try again
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-semibold"
            style={{ background: "var(--bg-surface-2)", color: "var(--text-secondary)", border: "1px solid var(--border)" }}
          >
            <RefreshCw size={12} /> Reload
          </button>
        </div>
      </div>
    </div>
  );
}
