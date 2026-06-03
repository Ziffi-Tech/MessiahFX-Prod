import { type NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";

const GATEWAY = process.env.GATEWAY_URL ?? "http://localhost:8080";

async function handler(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> }
) {
  // Check auth cookie
  const jar = await cookies();
  if (!jar.get("mxauth")) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { path } = await context.params;
  const upstream = `${GATEWAY}/${path.join("/")}${req.nextUrl.search}`;

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
    console.error("[gateway proxy]", err);
    return NextResponse.json({ error: "Gateway unreachable" }, { status: 502 });
  }
}

export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE };
