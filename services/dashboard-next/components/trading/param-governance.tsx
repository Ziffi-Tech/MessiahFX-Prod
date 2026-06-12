"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, ChevronRight, ChevronDown } from "lucide-react";
import { api } from "@/lib/api";
import type { ParamHistoryEntry } from "@/types/api";

export function ParamGovernancePanel({ strategies }: { strategies: string[] }) {
  return (
    <div className="panel">
      <div className="flex items-center gap-2 px-4 py-3 border-b" style={{ borderColor: "var(--border)" }}>
        <GitBranch size={14} style={{ color: "var(--purple)" }} />
        <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Parameter Governance</span>
        <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>versioned · audited · drift-checked</span>
      </div>
      <div>
        {strategies.map((s) => <GovernanceRow key={s} strategyType={s} />)}
      </div>
    </div>
  );
}

function GovernanceRow({ strategyType }: { strategyType: string }) {
  const [open, setOpen] = useState(false);

  const cur = useQuery({
    queryKey: ["governance", strategyType],
    queryFn: () => api.governance.getParams(strategyType),
    retry: false,
  });
  const hist = useQuery({
    queryKey: ["governance", "history", strategyType],
    queryFn: () => api.governance.history(strategyType, 10),
    enabled: open,
    retry: false,
  });

  // Not in the governed registry (strategy_configs) → skip silently.
  if (cur.isError || !cur.data) return null;
  const d = cur.data;

  return (
    <div style={{ borderBottom: "1px solid var(--border-subtle)" }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-4 py-2.5 text-left hover:bg-[var(--bg-hover)]"
      >
        {open ? <ChevronDown size={13} style={{ color: "var(--text-tertiary)" }} /> : <ChevronRight size={13} style={{ color: "var(--text-tertiary)" }} />}
        <span className="text-xs font-medium flex-1" style={{ color: "var(--text-primary)" }}>
          {strategyType.replace(/_/g, " ")}
        </span>
        <span className="badge badge-gray">v{d.version}</span>
        <span className="mono text-[10px]" style={{ color: "var(--text-tertiary)" }}>{d.hash}</span>
        <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>by {d.updated_by}</span>
      </button>

      {open && (
        <div className="px-4 pb-3 space-y-3">
          <div>
            <div className="text-[10px] mb-1" style={{ color: "var(--text-tertiary)" }}>Live parameters</div>
            <pre className="mono text-[11px] p-2.5 rounded overflow-x-auto" style={{ background: "var(--bg-surface-2)", color: "var(--text-secondary)" }}>
              {JSON.stringify(d.params, null, 2)}
            </pre>
          </div>

          <div>
            <div className="text-[10px] mb-1" style={{ color: "var(--text-tertiary)" }}>Change history</div>
            {hist.isLoading ? (
              <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>Loading…</p>
            ) : !hist.data?.history.length ? (
              <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>No recorded changes yet.</p>
            ) : (
              <div className="space-y-1.5">
                {hist.data.history.map((h, i) => <HistoryRow key={i} entry={h} />)}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function HistoryRow({ entry }: { entry: ParamHistoryEntry }) {
  const changed = entry.diff?.changed ?? {};
  const changedKeys = Object.keys(changed);
  return (
    <div className="flex items-start gap-2 text-[11px] py-1" style={{ borderTop: "1px solid var(--border-subtle)" }}>
      <span className="badge badge-gray">v{entry.version}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="badge badge-blue">{entry.source ?? "manual"}</span>
          <span style={{ color: "var(--text-secondary)" }}>{entry.reason || "—"}</span>
        </div>
        {changedKeys.length > 0 && (
          <div className="mono text-[10px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>
            {changedKeys.map((k) => `${k}: ${JSON.stringify(changed[k].old)}→${JSON.stringify(changed[k].new)}`).join("  ·  ")}
          </div>
        )}
      </div>
      <span className="mono text-[10px] whitespace-nowrap" style={{ color: "var(--text-tertiary)" }}>
        {entry.by} · {(entry.created_at || "").slice(0, 10)}
      </span>
    </div>
  );
}
