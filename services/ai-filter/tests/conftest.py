"""pytest configuration for ai-filter service tests.

Env vars are set by the repo-root conftest.py before this runs.
We only need to add this service's root to sys.path.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
