"use client";

import { useEffect, useState, type ReactNode } from "react";

interface StatCardProps {
  label: string;
  valuePath: string;
  valueKey: string;
  prefix?: string;
  suffix?: string;
  decimals?: number;
  signed?: boolean;
  invert?: boolean; // invert the colour (lower = green, higher = red)
  icon?: ReactNode;
}

export function StatCard({
  label,
  valuePath,
  valueKey,
  prefix = "",
  suffix = "",
  decimals = 0,
  signed = false,
  invert = false,
  icon,
}: StatCardProps) {
  const [value, setValue] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(valuePath, { cache: "no-store" });
        const json = await res.json();
        setValue(json[valueKey] ?? null);
      } catch {
        setValue(null);
      } finally {
        setLoading(false);
      }
    };
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [valuePath, valueKey]);

  const isPositive = value !== null && (invert ? value <= 0 : value >= 0);
  const color = value === null || value === 0
    ? "var(--text-secondary)"
    : isPositive ? "var(--green)" : "var(--red)";

  const display = value === null
    ? "—"
    : `${signed && value > 0 ? "+" : ""}${prefix}${value.toFixed(decimals)}${suffix}`;

  return (
    <div className="panel p-4 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{label}</span>
        <span style={{ color: "var(--text-tertiary)" }}>{icon}</span>
      </div>
      {loading ? (
        <div className="h-6 w-24 rounded animate-pulse" style={{ background: "var(--bg-surface-2)" }} />
      ) : (
        <div className="mono text-xl font-bold" style={{ color }}>
          {display}
        </div>
      )}
    </div>
  );
}
