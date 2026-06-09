import { type NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { verifySession, sessionSecret, SESSION_COOKIE, canWrite } from "@/lib/auth";
import { isRevoked } from "@/lib/revocation";

// ── Service routing map ────────────────────────────────────────────────────────
// DEFAULT (production + normal dev): every request goes to the gateway, which
// reverse-proxies to the internal services (see gateway/app/routes/proxy.py).
// This is the only correct behaviour inside the container, where the individual
// services are NOT reachable on localhost — only the gateway is.
//
// OPT-IN (host-side next dev WITHOUT the gateway running): set
// NEXT_PUBLIC_USE_SERVICE_ROUTING=true to fan out directly to each service's
// host port instead. Used rarely, for isolated single-service debugging.

const GATEWAY = process.env.GATEWAY_URL ?? "http://localhost:8080";
const USE_SERVICE_ROUTING = process.env.NEXT_PUBLIC_USE_SERVICE_ROUTING === "true";

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

  // Opt-in direct service routing for isolated host-side dev only.
  if (USE_SERVICE_ROUTING && prefix in SERVICE_PORTS) {
    const base = SERVICE_PORTS[prefix];
    const rest = pathSegments.slice(1).join("/");
    return rest ? `${base}/${rest}` : base;
  }

  // Default: the gateway proxies everything (health, control, signals, journal,
  // risk, strategy, backtest, ai, market-data, and the /stream SSE endpoint).
  return `${GATEWAY}/${pathSegments.join("/")}`;
}

async function handler(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  const session = token ? await verifySession(token, sessionSecret()) : null;
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // Stateless tokens can still be revoked (admin sign-out-all / per-user).
  if (await isRevoked(session.sub, session.iat)) {
    return NextResponse.json({ error: "Session revoked" }, { status: 401 });
  }

  // RBAC: viewers are read-only — block every mutating method.
  const isWrite = !["GET", "HEAD"].includes(req.method);
  if (isWrite && !canWrite(session.role)) {
    return NextResponse.json({ error: "Forbidden — your role is read-only" }, { status: 403 });
  }

  const { path } = await context.params;
  const upstream = resolveUpstream(path) + req.nextUrl.search;

  const headers = new Headers(req.headers);
  headers.delete("host");
  headers.delete("cookie");
  // Attribute downstream actions (kill switch, toggles, …) to the real operator.
  // Forward the signed token too so the gateway can VERIFY it (defense in depth),
  // not just trust these headers.
  headers.set("x-mezna-user", session.sub);
  headers.set("x-mezna-role", session.role);
  if (token) headers.set("x-mezna-token", token);

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
