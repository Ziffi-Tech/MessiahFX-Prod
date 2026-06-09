import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { SESSION_COOKIE } from "@/lib/auth";

// POST /api/auth/logout — clears the session and returns to the login page.
// Used by the sidebar sign-out form (a plain form POST), so it redirects.
export async function POST(req: NextRequest) {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
  return NextResponse.redirect(new URL("/login", req.url));
}
