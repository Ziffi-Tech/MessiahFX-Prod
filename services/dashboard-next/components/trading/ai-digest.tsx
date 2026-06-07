"use client";

import { Bot, RefreshCw } from "lucide-react";
import { useAiDigest } from "@/lib/hooks";
import { useQueryClient } from "@tanstack/react-query";
import { QK } from "@/lib/hooks";

export function AiDigest() {
  const { data, isLoading, isFetching } = useAiDigest();
  const qc = useQueryClient();

  const refresh = () => void qc.invalidateQueries({ queryKey: QK.digest });

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
          onClick={refresh}
          disabled={isFetching}
          className="p-1 rounded transition-colors hover:bg-[var(--bg-hover)] disabled:opacity-40"
          style={{ color: "var(--text-tertiary)" }}
          aria-label="Refresh digest"
        >
          <RefreshCw size={11} className={isFetching ? "animate-spin" : ""} />
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {[80, 70, 55].map((w) => (
            <div
              key={w}
              className="h-3 rounded animate-pulse"
              style={{ background: "var(--bg-surface-2)", width: `${w}%` }}
            />
          ))}
        </div>
      ) : data?.digest ? (
        <>
          <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            {data.digest}
          </p>
          {data.generated_at && (
            <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
              Generated {new Date(data.generated_at).toLocaleTimeString()}
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
