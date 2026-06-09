"use client";

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

interface StatCardProps {
  label:      string;
  queryKey:   readonly unknown[];
  queryFn:    () => Promise<Record<string, unknown>>;
  valueKey:   string;
  prefix?:    string;
  suffix?:    string;
  decimals?:  number;
  signed?:    boolean;
  /** Invert colour logic: lower value = green (e.g. drawdown) */
  invert?:    boolean;
  icon?:      ReactNode;
  refetchInterval?: number;
}

export function StatCard({
  label, queryKey, queryFn, valueKey,
  prefix = "", suffix = "", decimals = 0,
  signed = false, invert = false, icon,
  refetchInterval = 10_000,
}: StatCardProps) {
  const { data, isLoading } = useQuery({
    queryKey,
    queryFn,
    refetchInterval,
  });

  const raw = data?.[valueKey];
  const value = raw !== undefined && raw !== null ? Number(raw) : null;

  const isPositive = value !== null && (invert ? value <= 0 : value >= 0);
  const color =
    value === null || value === 0
      ? "var(--text-secondary)"
      : isPositive
      ? "var(--green)"
      : "var(--red)";

  const display =
    value === null
      ? "—"
      : `${signed && value > 0 ? "+" : ""}${prefix}${value.toFixed(decimals)}${suffix}`;

  return (
    <div className="panel p-4 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
          {label}
        </span>
        <span style={{ color: "var(--text-tertiary)" }}>{icon}</span>
      </div>
      {isLoading ? (
        <div
          className="h-6 w-24 rounded animate-pulse"
          style={{ background: "var(--bg-surface-2)" }}
        />
      ) : (
        <div className="mono text-xl font-bold" style={{ color }}>
          {display}
        </div>
      )}
    </div>
  );
}
