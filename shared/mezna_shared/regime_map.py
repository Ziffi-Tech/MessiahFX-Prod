"""
Canonical market-regime → preferred-strategy mapping.

Single source of truth shared by:
  - strategy.app.strategies.rotation  (rotation engine: degrade → pick alternative)
  - executor.app.consumer             (_update_rotation_preferred after a degrade)

This table was previously duplicated verbatim in both modules. Any edit had to
be made in two places or the rotation behaviour would silently diverge between
the strategy and executor services. It now lives here only.
"""

from __future__ import annotations

# Ordered best-first list of strategies to prefer for each market regime.
REGIME_STRATEGY_MAP: dict[str, list[str]] = {
    "trending_bull":   ["momentum", "breakout", "swing", "stat_arb", "funding_arb"],
    "trending_bear":   ["breakout", "momentum", "stat_arb", "swing", "funding_arb"],
    "ranging":         ["mean_reversion_scalp", "stat_arb", "funding_arb", "swing"],
    "high_volatility": ["funding_arb", "stat_arb", "mean_reversion_scalp", "breakout"],
    "low_volatility":  ["mean_reversion_scalp", "funding_arb", "stat_arb", "swing"],
    "unknown":         ["funding_arb", "stat_arb", "mean_reversion_scalp", "swing", "breakout", "momentum"],
}

# All known strategy identifiers (degraded-state lookups, validation).
ALL_STRATEGIES: frozenset[str] = frozenset({
    "funding_arb", "stat_arb", "swing",
    "breakout", "mean_reversion_scalp", "momentum",
})


def preferred_for_regime(regime: str) -> list[str]:
    """
    Return the best-first preferred-strategy list for a regime.

    Falls back to the regime-neutral "unknown" ordering for any unrecognised
    or missing regime label, so callers never have to special-case it.
    """
    return REGIME_STRATEGY_MAP.get(regime, REGIME_STRATEGY_MAP["unknown"])
