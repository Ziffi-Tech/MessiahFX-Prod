"""
Root conftest.py — runs before any test collection.

Sets environment variables required by service Settings() classes
at import time. Each service's own conftest.py adds only its own
root to sys.path, preventing `app.*` package name collisions when
all three services share the same package name "app".

Note: test directories must NOT contain __init__.py. Without those
files, pytest's --import-mode=importlib generates unique synthetic
module names for each conftest.py, eliminating the name collision
that would otherwise occur because all three are in dirs named `tests`.
"""
import os

# ── Env vars required by service Settings() at import time ───────────────────
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-for-unit-tests")
