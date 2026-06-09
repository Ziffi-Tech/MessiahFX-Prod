"use client";

import { useLiveStream } from "@/lib/stream";

/**
 * Invisible mount point that opens the single app-wide SSE connection.
 * Rendered once inside the trading layout so every panel sees live data.
 */
export function StreamConnector() {
  useLiveStream();
  return null;
}
