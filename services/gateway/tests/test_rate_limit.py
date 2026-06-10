"""Tests for the gateway rate-limit ASGI middleware."""

import asyncio

from app.middleware import RateLimitMiddleware


class FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}

    async def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, seconds):
        return True


class FakeState:
    def __init__(self, redis):
        self.redis = redis


class FakeApp:
    """Stands in for scope['app'] — only needs .state.redis."""
    def __init__(self, redis):
        self.state = FakeState(redis)


async def _inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _scope(app, path="/journal/trades", method="GET", user=b"alice"):
    headers = [(b"x-mezna-user", user)] if user else []
    return {"type": "http", "path": path, "method": method, "headers": headers,
            "app": app, "client": ("1.2.3.4", 1234)}


def _status(messages):
    for m in messages:
        if m["type"] == "http.response.start":
            return m["status"]
    return None


async def _call(mw, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)

    await mw(scope, receive, send)
    return _status(sent)


def test_blocks_over_limit():
    app = FakeApp(FakeRedis())
    mw = RateLimitMiddleware(_inner, enabled=True, limit=3, window=60)
    statuses = [asyncio.run(_call(mw, _scope(app))) for _ in range(4)]
    assert statuses == [200, 200, 200, 429]


def test_per_user_isolated():
    app = FakeApp(FakeRedis())
    mw = RateLimitMiddleware(_inner, enabled=True, limit=2, window=60)
    # alice exhausts her budget…
    assert [asyncio.run(_call(mw, _scope(app, user=b"alice"))) for _ in range(3)] == [200, 200, 429]
    # …bob is unaffected.
    assert asyncio.run(_call(mw, _scope(app, user=b"bob"))) == 200


def test_exempt_paths_never_limited():
    app = FakeApp(FakeRedis())
    mw = RateLimitMiddleware(_inner, enabled=True, limit=1, window=60)
    for path in ("/health/live", "/stream", "/metrics"):
        statuses = [asyncio.run(_call(mw, _scope(app, path=path))) for _ in range(5)]
        assert all(s == 200 for s in statuses), path


def test_options_exempt():
    app = FakeApp(FakeRedis())
    mw = RateLimitMiddleware(_inner, enabled=True, limit=1, window=60)
    statuses = [asyncio.run(_call(mw, _scope(app, method="OPTIONS"))) for _ in range(5)]
    assert all(s == 200 for s in statuses)


def test_disabled_passthrough():
    app = FakeApp(FakeRedis())
    mw = RateLimitMiddleware(_inner, enabled=False, limit=1, window=60)
    statuses = [asyncio.run(_call(mw, _scope(app))) for _ in range(5)]
    assert all(s == 200 for s in statuses)


def test_fails_open_without_redis():
    app = FakeApp(None)  # no redis on state
    mw = RateLimitMiddleware(_inner, enabled=True, limit=1, window=60)
    statuses = [asyncio.run(_call(mw, _scope(app))) for _ in range(5)]
    assert all(s == 200 for s in statuses)
