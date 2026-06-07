import { type NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

// ── Service routing map ────────────────────────────────────────────────────────
// In production (containerised): all traffic goes to gateway which proxies internally.
// In development (host-side next dev): route directly to each service's host port.
// GATEWAY_URL env var switches the mode — if it points to the real gateway,
// the gateway handles proxy. If unset, we fan-out to individual service ports.

const GATEWAY = process.env.GATEWAY_URL ?? "http://localhost:8080";

// Individual service base URLs (used when NEXT_PUBLIC_USE_SERVICE_ROUTING=true
// or when the path prefix matches a known service that the gateway doesn't proxy)
const SERVICE_PORTS: Record<string, string> = {
  "journal":     process.env.JOURNAL_URL      ?? "http://localhost:8006",
  "risk":        process.env.RISK_URL         ?? "http://localhost:8003",
  "strategy":    process.env.STRATEGY_URL     ?? "http://localhost:8002",
  "backtest":    process.env.BACKTEST_URL     ?? "http://localhost:8008",
  "ai":          process.env.AI_FILTER_URL    ?? "http://localhost:8005",
  "market-data": process.env.MARKET_DATA_URL  ?? "http://localhost:8001",
};

function resolveUpstream(pathSegments: string[]): string {
  if (!pathSegments.length) return GATEWAY;
  const prefix = pathSegments[0];

  // If a dedicated env var is set for this prefix, use it (service direct routing)
  if (prefix in SERVICE_PORTS) {
    const base = SERVICE_PORTS[prefix];
    // Rest of path after prefix
    const rest = pathSegments.slice(1).join("/");
    return rest ? `${base}/${rest}` : base;
  }

  // Fallback: everything else goes to the gateway (health, control, signals, etc.)
  return `${GATEWAY}/${pathSegments.join("/")}`;
}

async function handler(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  const jar = await cookies();
  if (!jar.get("mxauth")) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { path } = await context.params;
  const upstream = resolveUpstream(path) + req.nextUrl.search;

  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("cookie");

  let body: BodyInit | undefined;
  if (!["GET", "HEAD"].includes(req.method)) {
    body = req.body ?? undefined;
  }

  try {
    const res = await fetch(upstream, {
      method: req.method,
      headers,
      body,
      duplex: "half",
    } as RequestInit);

    const resHeaders = new Headers(res.headers);
    resHeaders.delete("transfer-encoding");

    return new NextResponse(res.body, {
      status: res.status,
      headers: resHeaders,
    });
  } catch (err) {
    console.error("[gateway proxy]", upstream, err);
    return NextResponse.json(
      { error: "Service unreachable", upstream },
      { status: 502 }
    );
  }
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE };
