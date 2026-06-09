import { NextRequest, NextResponse } from "next/server";
import { verifySession, sessionSecret, SESSION_COOKIE } from "@/lib/auth";

// Next 16 middleware (proxy). Verifies the signed session token — not just cookie
// presence — and gates the app. API calls get a 401 JSON; pages redirect to login.
export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;

  // Login page and the auth endpoints are always reachable.
  if (pathname.startsWith("/login") || pathname.startsWith("/api/auth")) {
    return NextResponse.next();
  }

  // Internal Next.js assets.
  if (pathname.startsWith("/_next") || pathname.startsWith("/favicon")) {
    return NextResponse.next();
  }

  const token = req.cookies.get(SESSION_COOKIE)?.value;
  const session = token ? await verifySession(token, sessionSecret()) : null;

  if (!session) {
    if (pathname.startsWith("/api")) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    return NextResponse.redirect(new URL("/login", req.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
