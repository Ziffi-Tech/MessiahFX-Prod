"""
Gateway edge middleware.

RateLimitMiddleware — a pure ASGI fixed-window limiter backed by Redis.

Pure ASGI (not BaseHTTPMiddleware) on purpose: it passes the send callable
straight through, so it never buffers a response — critical for the long-lived
SSE /stream endpoint, which BaseHTTPMiddleware would break.

Keyed by the operator (X-Mezna-User) when present, else the client IP, so one
flooding client can't starve the others. Health checks, the SSE stream, and CORS
preflight (OPTIONS) are exempt. Fails OPEN on any Redis error — the limiter must
never take the gateway down.
"""

import time

import structlog

log = structlog.get_logger()

_EXEMPT_PREFIXES = ("/health", "/stream", "/metrics")
_RATE_LIMIT_BODY = b'{"error":"rate limit exceeded"}'


class RateLimitMiddleware:
    def __init__(self, app, *, enabled: bool, limit: int, window: int):
        self.app = app
        self.enabled = enabled
        self.limit = limit
        self.window = max(1, window)

    def _client_id(self, scope) -> str:
        headers = dict(scope.get("headers") or [])
        user = headers.get(b"x-mezna-user")
        if user:
            return f"user:{user.decode('latin-1')}"
        # X-Forwarded-For first hop, else the socket peer.
        xff = headers.get(b"x-forwarded-for")
        if xff:
            return f"ip:{xff.decode('latin-1').split(',')[0].strip()}"
        client = scope.get("client")
        return f"ip:{client[0]}" if client else "ip:unknown"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled:
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if scope.get("method") == "OPTIONS" or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await self.app(scope, receive, send)

        redis = getattr(getattr(scope.get("app"), "state", None), "redis", None)
        if redis is None:
            return await self.app(scope, receive, send)  # fail open (startup/no redis)

        bucket = int(time.time()) // self.window
        key = f"ratelimit:{self._client_id(scope)}:{bucket}"
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, self.window)
        except Exception as exc:
            log.warning("ratelimit.redis_error", error=str(exc))
            return await self.app(scope, receive, send)  # fail open

        if count > self.limit:
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(self.window).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": _RATE_LIMIT_BODY})
            return

        return await self.app(scope, receive, send)
