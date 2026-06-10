"use client";

import { useEffect, useRef, useState } from "react";

/**
 * A numeric cell that briefly flashes green/red when its value changes — the
 * classic live-blotter tell. Driven by the SSE tick store, so prices pulse in
 * real time.
 */
export function FlashCell({
  value,
  format,
  color,
  className = "",
}: {
  value: number | null | undefined;
  format?: (n: number) => string;
  color?: string;
  className?: string;
}) {
  const prev = useRef<number | null | undefined>(value);
  const [flash, setFlash] = useState<"up" | "down" | null>(null);

  useEffect(() => {
    if (value != null && prev.current != null && value !== prev.current) {
      setFlash(value > prev.current ? "up" : "down");
      const id = setTimeout(() => setFlash(null), 450);
      prev.current = value;
      return () => clearTimeout(id);
    }
    prev.current = value;
  }, [value]);

  const bg = flash === "up" ? "var(--green-dim)" : flash === "down" ? "var(--red-dim)" : "transparent";

  return (
    <span
      className={`mono ${className}`}
      style={{ background: bg, color, transition: "background 0.45s ease", borderRadius: 3, padding: "1px 5px" }}
    >
      {value == null ? "—" : format ? format(value) : value}
    </span>
  );
}
