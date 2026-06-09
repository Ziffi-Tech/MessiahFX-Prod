// ─── Session revocation check (server-only) ──────────────────────────────────
// Signed-session tokens are stateless, so to revoke before expiry we ask the
// gateway for the revocation epochs and reject tokens issued before them. The
// result is cached per server instance (TTL) so this is ~one gateway call per
// REVOCATION_TTL_MS, not one per request. Fails OPEN on gateway errors — a blip
// must not lock everyone out (no data flows if the gateway is down anyway).

const GATEWAY = process.env.GATEWAY_URL ?? "http://localhost:8080";
const REVOCATION_TTL_MS = 15_000;

interface Revocations { all: number; users: Record<string, number> }

let cache: { at: number; data: Revocations } | null = null;

async function load(): Promise<Revocations> {
  const now = Date.now();
  if (cache && now - cache.at < REVOCATION_TTL_MS) return cache.data;
  try {
    const res = await fetch(`${GATEWAY}/api/v1/control/revocations`, { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    const data = (await res.json()) as Revocations;
    cache = { at: now, data: { all: data.all ?? 0, users: data.users ?? {} } };
    return cache.data;
  } catch {
    // Fail open: reuse stale cache if we have one, else treat as "nothing revoked".
    return cache?.data ?? { all: 0, users: {} };
  }
}

/** True when a token (sub, iat) has been revoked globally or for that user. */
export async function isRevoked(sub: string, iat: number): Promise<boolean> {
  const { all, users } = await load();
  if (iat < (all ?? 0)) return true;
  const userEpoch = users?.[sub];
  return typeof userEpoch === "number" && iat < userEpoch;
}
