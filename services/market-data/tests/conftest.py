"""pytest configuration for market-data service tests.

Env vars are set by the repo-root conftest.py before this runs.
Adds this service's root to sys.path, plus the shared package root — the
tested module (app.backfill) imports mezna_shared, which is pip-installed in
the container but resolved via sys.path for local test runs.

Run per-service (the supported mode): python -m pytest services/market-data/tests
"""
import sys
import os

_SERVICE_ROOT = os.path.join(os.path.dirname(__file__), "..")
_SHARED_ROOT = os.path.join(_SERVICE_ROOT, "..", "..", "shared")
for _p in (_SERVICE_ROOT, _SHARED_ROOT):
    _abs = os.path.abspath(_p)
    if _abs not in sys.path:
        sys.path.insert(0, _abs)
