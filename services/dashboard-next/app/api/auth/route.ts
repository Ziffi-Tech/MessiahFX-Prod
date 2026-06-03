import { NextResponse } from "next/server";
import { cookies } from "next/headers";

const PASSWORD = process.env.DASHBOARD_PASSWORD ?? "mezna";

// POST /api/auth — login
export async function POST(req: Request) {
  const body = await req.json().catch(() => ({})) as Record<string, unknown>;
  const { password } = body as { password?: string };

  if (password !== PASSWORD) {
    return NextResponse.json({ error: "Invalid password" }, { status: 401 });
  }

  const jar = await cookies();
  jar.set("mxauth", "1", {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: 60 * 60 * 24 * 30, // 30 days
    path: "/",
  });

  return NextResponse.json({ ok: true });
}

// DELETE /api/auth — logout
export async function DELETE() {
  const jar = await cookies();
  jar.delete("mxauth");
  return NextResponse.redirect(new URL("/login", process.env.NEXTAUTH_URL ?? "http://localhost:3000"));
}
