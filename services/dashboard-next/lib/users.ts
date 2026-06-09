// ─── User roster + credential verification (Node runtime only) ──────────────
// Imported only by the login route — uses node:crypto, so it must never be
// pulled into the Edge middleware (proxy.ts). Keep this file server-only.

import { scryptSync, timingSafeEqual } from "node:crypto";
import type { Role } from "./auth";

interface UserRecord {
  username: string;
  password: string;   // "scrypt$<saltHex>$<hashHex>" (preferred) or plaintext (dev)
  role: Role;
}

const VALID_ROLES: Role[] = ["admin", "operator", "viewer"];

/**
 * Parse DASHBOARD_USERS — a JSON array of {username, password, role}.
 * Returns [] when unset/invalid, which triggers legacy single-password mode.
 */
function roster(): UserRecord[] {
  const raw = process.env.DASHBOARD_USERS;
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw) as UserRecord[];
    return arr
      .filter((u) => u?.username && u?.password)
      .map((u) => ({
        username: String(u.username),
        password: String(u.password),
        role: VALID_ROLES.includes(u.role) ? u.role : "operator",
      }));
  } catch {
    console.error("[auth] DASHBOARD_USERS is not valid JSON — ignoring");
    return [];
  }
}

function safeEqualStr(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  return ab.length === bb.length && timingSafeEqual(ab, bb);
}

function verifyPassword(stored: string, provided: string): boolean {
  if (stored.startsWith("scrypt$")) {
    const [, saltHex, hashHex] = stored.split("$");
    if (!saltHex || !hashHex) return false;
    const salt = Buffer.from(saltHex, "hex");
    const expected = Buffer.from(hashHex, "hex");
    const actual = scryptSync(provided, salt, expected.length);
    return expected.length === actual.length && timingSafeEqual(expected, actual);
  }
  // Plaintext fallback (dev only). Hash production passwords — see scripts/hash-password.mjs.
  return safeEqualStr(stored, provided);
}

/**
 * Verify credentials and return the authenticated identity, or null.
 *
 * Roster mode: match username, verify password (scrypt or plaintext).
 * Legacy mode (no DASHBOARD_USERS): any username + the shared DASHBOARD_PASSWORD,
 * granted the admin role — preserves the previous single-password behaviour.
 */
export function authenticate(username: string, password: string): { username: string; role: Role } | null {
  const users = roster();

  if (users.length === 0) {
    const legacy = process.env.DASHBOARD_PASSWORD ?? "mezna";
    if (password && safeEqualStr(legacy, password)) {
      return { username: username.trim() || "operator", role: "admin" };
    }
    return null;
  }

  const user = users.find((u) => u.username === username);
  if (!user) return null;
  return verifyPassword(user.password, password) ? { username: user.username, role: user.role } : null;
}
