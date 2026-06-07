"""Put the shared package root on sys.path so `import mezna_shared` resolves.

Mirrors the per-service conftest pattern: each tests dir adds only its own
package root. No __init__.py here — pytest runs in --import-mode=importlib.
"""
import os
import sys

_SHARED_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SHARED_ROOT not in sys.path:
    sys.path.insert(0, _SHARED_ROOT)
