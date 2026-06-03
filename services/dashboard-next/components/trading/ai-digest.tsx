"use client";

import { useEffect, useState } from "react";
import { Bot, RefreshCw } from "lucide-react";

export function AiDigest() {
  const [digest, setDigest] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [ts, setTs] = useState<string>("");

  const load = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/gateway/ai-filter/digest", { cache: "no-store" });
      if (res.ok) {
        const json = await res.json();
        setDigest(json.digest);
        setTs(new Date(json.generated_at).toLocaleTimeString());
      }
    } catch { /* */ } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="panel ai-panel p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Bot size={13} style={{ color: "var(--purple)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
            AI Digest
          </span>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40"
          style={{ color: "var(--text-tertiary)" }}
        >
          <RefreshCw size={11} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      {loading ? (
        <div className="space-y-2">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-3 rounded animate-pulse" style={{ background: "var(--bg-surface-2)", width: `${80 - i * 10}%` }} />
          ))}
        </div>
      ) : digest ? (
        <>
          <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            {digest}
          </p>
          {ts && (
            <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              Generated {ts}
            </p>
          )}
        </>
      ) : (
        <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          No digest available — AI filter may be offline
        </p>
      )}
    </div>
  );
}
