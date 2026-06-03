"use client";

import { useEffect, useState } from "react";
import { Cpu } from "lucide-react";

const REGIME_COLOURS: Record<string, string> = {
  trending_bull:  "badge-green",
  trending_bear:  "badge-red",
  ranging:        "badge-blue",
  high_volatility:"badge-orange",
  low_volatility: "badge-gray",
  unknown:        "badge-gray",
};

const REGIME_LABELS: Record<string, string> = {
  trending_bull:  "BULL",
  trending_bear:  "BEAR",
  ranging:        "RANGING",
  high_volatility:"HIGH VOL",
  low_volatility: "LOW VOL",
  unknown:        "UNKNOWN",
};

export function RegimeBadge() {
  const [regime, setRegime] = useState<string>("unknown");

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/gateway/ai-filter/regime", { cache: "no-store" });
        const json = await res.json();
        setRegime(json.regime ?? "unknown");
      } catch { /* fail silently */ }
    };
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  const cls = REGIME_COLOURS[regime] ?? "badge-gray";
  const label = REGIME_LABELS[regime] ?? regime.toUpperCase();

  return (
    <div className="flex items-center gap-2">
      <Cpu size={13} style={{ color: "var(--purple)" }} />
      <span className="text-xs" style={{ color: "var(--text-secondary)" }}>Regime:</span>
      <span className={`badge ${cls}`}>{label}</span>
    </div>
  );
}
