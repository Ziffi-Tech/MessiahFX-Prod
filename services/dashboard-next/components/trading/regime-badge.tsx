"use client";

import { Cpu } from "lucide-react";
import { useRegime } from "@/lib/hooks";

const COLOUR: Record<string, string> = {
  trending_bull:   "badge-green",
  trending_bear:   "badge-red",
  ranging:         "badge-blue",
  high_volatility: "badge-orange",
  low_volatility:  "badge-gray",
  unknown:         "badge-gray",
};

const LABEL: Record<string, string> = {
  trending_bull:   "BULL",
  trending_bear:   "BEAR",
  ranging:         "RANGING",
  high_volatility: "HIGH VOL",
  low_volatility:  "LOW VOL",
  unknown:         "UNKNOWN",
};

export function RegimeBadge() {
  const { data } = useRegime();
  const regime = data?.regime ?? "unknown";

  return (
    <div className="flex items-center gap-2">
      <Cpu size={13} style={{ color: "var(--purple)" }} />
      <span className="text-xs" style={{ color: "var(--text-secondary)" }}>Regime:</span>
      <span className={`badge ${COLOUR[regime] ?? "badge-gray"}`}>
        {LABEL[regime] ?? regime.toUpperCase()}
      </span>
      {data?.confidence !== undefined && (
        <span className="text-[10px] mono" style={{ color: "var(--text-tertiary)" }}>
          {data.confidence}%
        </span>
      )}
    </div>
  );
}
