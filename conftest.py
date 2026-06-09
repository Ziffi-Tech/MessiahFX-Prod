"""
Root conftest.py — runs before any test collection.

Two responsibilities:

1. Set environment variables required by service Settings() classes at import time.

2. Make the full-suite `python -m pytest` work despite every service sharing the
   top-level package name `app`. In one process, sys.modules can only hold one
   `app`, so the first-imported service's `app` would win and the others' test
   modules would fail (`No module named 'app.checker'`). The pytest_collectstart
   hook below purges the cached `app` and points sys.path at the owning service
   IMMEDIATELY before each service test module is imported — the one reliable
   moment (per-conftest sys.path tricks lose because the last conftest wins).

Per-service runs (`pytest services/risk/tests`) are unaffected and still work.
Test dirs must NOT contain __init__.py (importlib mode needs unique module names).
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_SHARED_ROOT = os.path.join(_REPO_ROOT, "shared")
_SERVICES_MARKER = os.sep + "services" + os.sep

# ── Env vars required by service Settings() at import time ───────────────────
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")


def _front(path: str) -> None:
    """Move a path to the front of sys.path (de-duplicated)."""
    abs_path = os.path.abspath(path)
    while abs_path in sys.path:
        sys.path.remove(abs_path)
    sys.path.insert(0, abs_path)


def _service_root_for(path: str) -> str | None:
    """Return the .../services/<svc> root for a path under a service, else None."""
    if _SERVICES_MARKER not in path:
        return None
    after = path.split(_SERVICES_MARKER, 1)[1]
    svc = after.split(os.sep, 1)[0]
    return os.path.join(_REPO_ROOT, "services", svc)


def pytest_collectstart(collector) -> None:
    """
    Before a service test module is imported, make ITS `app` the one that resolves.

    Fires per collector (depth-first), so for a Module it runs right before the
    import — purge any other service's cached `app`, then put this service's root
    (and the shared root, for local runs) at the front of sys.path.
    """
    path = str(getattr(collector, "path", "") or getattr(collector, "fspath", ""))
    if not path.endswith(".py"):
        return
    service_root = _service_root_for(path)
    if service_root is None:
        return

    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        del sys.modules[name]

    _front(_SHARED_ROOT)
    _front(service_root)  # service root ends up first, ahead of shared
