"""
Parameter governance — version, audit, and drift-check strategy parameters.

A strategy's parameters (z-score thresholds, lookbacks, edge floors, …) decide
its behaviour, yet changing them has been a silent overwrite. This makes every
change traceable: a canonical hash to detect drift, a structured diff for the
audit trail, and a drift check so the params running live can be compared against
the set a backtest/walk-forward actually validated.

Pure + dependency-free; the gateway wires these to strategy_configs (current) +
audit_log (append-only history).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(params: dict) -> str:
    """Deterministic JSON regardless of key order (so equal params hash equal)."""
    return json.dumps(params or {}, sort_keys=True, separators=(",", ":"), default=str)


def param_hash(params: dict) -> str:
    """Short stable fingerprint of a parameter set."""
    return hashlib.sha256(canonical_json(params).encode("utf-8")).hexdigest()[:16]


def diff_params(old: dict, new: dict) -> dict:
    """
    Structured diff old → new:
      added   : keys only in new
      removed : keys only in old
      changed : keys in both whose value differs ({old, new})
    """
    old = old or {}
    new = new or {}
    added = {k: new[k] for k in new if k not in old}
    removed = {k: old[k] for k in old if k not in new}
    changed = {k: {"old": old[k], "new": new[k]} for k in new if k in old and old[k] != new[k]}
    return {"added": added, "removed": removed, "changed": changed}


def has_drift(a: dict, b: dict) -> bool:
    """True when two parameter sets are not identical (by canonical hash)."""
    return param_hash(a) != param_hash(b)


def drift_report(live: dict, reference: dict) -> dict:
    """Compare live params against a reference (e.g. a backtested set)."""
    return {
        "has_drift": has_drift(live, reference),
        "live_hash": param_hash(live),
        "reference_hash": param_hash(reference),
        "diff": diff_params(reference, live),  # reference → live
    }


def next_version(current_version: Any) -> int:
    """Monotonic version bump; tolerant of missing/garbage prior values."""
    try:
        return int(current_version) + 1
    except (TypeError, ValueError):
        return 1
