"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle, AlertTriangle, Rocket } from "lucide-react";
import { api } from "@/lib/api";
import type { ReadinessCriterion } from "@/types/api";

const LABELS: Record<string, string> = {
  paper_duration: "Paper duration",
  kill_switch_tested: "Kill switch tested",
  sufficient_trades: "Sufficient trades",
  round_trips_closed: "Round trips closed",
  still_paper: "Still in paper mode",
  risk_breaches: "No risk breaches",
};

function Row({ c, advisory = false }: { c: ReadinessCriterion; advisory?: boolean }) {
  const ok = c.pass;
  const Icon = ok ? CheckCircle2 : advisory ? AlertTriangle : XCircle;
  const color = ok ? "var(--green)" : advisory ? "var(--orange)" : "var(--red)";
  return (
    <div className="flex items-start gap-2.5 py-2 border-b" style={{ borderColor: "var(--border-subtle)" }}>
      <Icon size={14} style={{ color, marginTop: 1 }} />
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>
          {LABELS[c.name] ?? c.name}
        </div>
        <div className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{c.detail}</div>
      </div>
      <span className="mono text-[11px]" style={{ color }}>
        {c.value}{c.threshold ? ` / ${c.threshold}` : ""}
      </span>
    </div>
  );
}

export function ReadinessPanel() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["journal", "readiness"],
    queryFn: () => api.journal.readiness(),
    refetchInterval: 60_000,
  });

  return (
    <div className="panel p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Rocket size={14} style={{ color: "var(--blue)" }} />
          <span className="text-xs font-semibold" style={{ color: "var(--text-primary)" }}>Go-Live Readiness</span>
        </div>
        {data && (
          <span className={`badge ${data.ready ? "badge-green" : "badge-orange"}`}>
            {data.ready ? "READY" : "NOT READY"}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="h-24 animate-pulse rounded" style={{ background: "var(--bg-surface-2)" }} />
      ) : isError || !data ? (
        <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>Readiness data unavailable.</p>
      ) : (
        <>
          <div className="space-y-0">
            {data.criteria.map((c) => <Row key={c.name} c={c} />)}
            {data.advisory.map((c) => <Row key={c.name} c={c} advisory />)}
          </div>
          <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>
            Gate: {data.thresholds.min_paper_days}+ days paper · kill switch tested · ≥
            {data.thresholds.min_trades} trades · still paper. Advisory items don&apos;t block.
          </p>
        </>
      )}
    </div>
  );
}
