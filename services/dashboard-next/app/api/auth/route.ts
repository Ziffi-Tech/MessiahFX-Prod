import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { authenticate } from "@/lib/users";
import {
  signSession, verifySession, sessionSecret,
  SESSION_COOKIE, SESSION_TTL_SECONDS,
} from "@/lib/auth";
import { isRevoked } from "@/lib/revocation";

// POST /api/auth — login (username + password → signed session cookie)
export async function POST(req: Request) {
  const body = (await req.json().catch(() => ({}))) as { username?: string; password?: string };
  const user = authenticate(String(body.username ?? ""), String(body.password ?? ""));
  if (!user) {
    return NextResponse.json({ error: "Invalid credentials" }, { status: 401 });
  }

  const now = Math.floor(Date.now() / 1000);
  const token = await signSession(
    { sub: user.username, role: user.role, iat: now, exp: now + SESSION_TTL_SECONDS },
    sessionSecret(),
  );

  const jar = await cookies();
  jar.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: SESSION_TTL_SECONDS,
    path: "/",
  });

  return NextResponse.json({ ok: true, user: user.username, role: user.role });
}

// GET /api/auth — whoami (for the client to render the current user/role)
export async function GET() {
  const jar = await cookies();
  const token = jar.get(SESSION_COOKIE)?.value;
  const session = token ? await verifySession(token, sessionSecret()) : null;
  if (!session || (await isRevoked(session.sub, session.iat))) {
    return NextResponse.json({ authenticated: false }, { status: 401 });
  }
  return NextResponse.json({ authenticated: true, user: session.sub, role: session.role });
}

// DELETE /api/auth — logout (programmatic)
export async function DELETE() {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
  return NextResponse.json({ ok: true });
}
