// ─── Session tokens — HMAC-SHA256 signed, Edge- AND Node-safe (Web Crypto) ──
// A minimal JWS (HS256): base64url(payload).base64url(sig). No dependency, so it
// runs in proxy.ts (Edge middleware) and the Node API routes alike.

export type Role = "admin" | "operator" | "viewer";

export interface SessionPayload {
  sub: string;   // username
  role: Role;
  iat: number;   // issued-at (epoch seconds)
  exp: number;   // expiry (epoch seconds)
}

export const SESSION_COOKIE = "mxauth";
export const SESSION_TTL_SECONDS = 60 * 60 * 24 * 7; // 7 days

const dec = new TextDecoder();

// Always produce ArrayBuffer-backed views so the Web Crypto BufferSource types
// are satisfied across runtimes (TS 5.7 narrows Uint8Array over its buffer type).
function toBytes(s: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(s, (c) => c.charCodeAt(0) & 0xff);
}

function utf8(s: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(new TextEncoder().encode(s));
}

function b64urlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(s: string): Uint8Array<ArrayBuffer> {
  const norm = s.replace(/-/g, "+").replace(/_/g, "/");
  const pad = norm.length % 4 ? 4 - (norm.length % 4) : 0;
  return toBytes(atob(norm + "=".repeat(pad)));
}

async function hmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw", utf8(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"],
  );
}

export async function signSession(payload: SessionPayload, secret: string): Promise<string> {
  const body = b64urlEncode(utf8(JSON.stringify(payload)));
  const key = await hmacKey(secret);
  const sig = await crypto.subtle.sign("HMAC", key, utf8(body));
  return `${body}.${b64urlEncode(new Uint8Array(sig))}`;
}

export async function verifySession(token: string, secret: string): Promise<SessionPayload | null> {
  const parts = token.split(".");
  if (parts.length !== 2) return null;
  const [body, sig] = parts;
  try {
    const key = await hmacKey(secret);
    const ok = await crypto.subtle.verify("HMAC", key, b64urlDecode(sig), utf8(body));
    if (!ok) return null;
    const payload = JSON.parse(dec.decode(b64urlDecode(body))) as SessionPayload;
    if (typeof payload.exp !== "number" || payload.exp * 1000 < Date.now()) return null;
    if (payload.role !== "admin" && payload.role !== "operator" && payload.role !== "viewer") return null;
    return payload;
  } catch {
    return null;
  }
}

/**
 * Signing secret. Prefer SESSION_SECRET; fall back to DASHBOARD_PASSWORD so a
 * legacy single-password deployment keeps working. The literal dev fallback only
 * applies when neither is set — set SESSION_SECRET in any real deployment.
 */
export function sessionSecret(): string {
  return process.env.SESSION_SECRET || process.env.DASHBOARD_PASSWORD || "mezna-dev-secret-change-me";
}

/** Write actions (control, toggles, mutating methods) require operator or admin. */
export function canWrite(role: Role): boolean {
  return role === "admin" || role === "operator";
}
