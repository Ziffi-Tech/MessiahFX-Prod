"""
Hard pre-trade risk checks — pure, stateless functions.

These checks run in strict priority order. The first failure immediately
returns a rejection. No partial approval is possible.

Check order (highest priority first):
  1. kill_switch         — risk:halt flag; blocks ALL activity immediately
  2. strategy_enabled    — dashboard toggle; operator can disable per-strategy
  3. strategy_cooldown   — risk:cooldown:{strategy} TTL key (set after loss streak)
  4. daily_drawdown      — total loss today >= limit → REJECT + AUTO-HALT
  5. max_open_positions  — too many simultaneous positions → REJECT
  6. consecutive_losses  — loss streak >= limit → REJECT + trigger cooldown
  7. net_edge_positive   — final sanity check; should never fail if strategy is correct

Stateful side-effects (auto-halt, cooldown) are flagged in the result but
executed by the consumer — this module stays pure and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Settings


@dataclass
class CheckResult:
    """Result of a pre-trade risk check run."""

    approved: bool
    rejection_reason: str | None = None
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)

    # Side-effect flags — consumer acts on these after routing
    auto_halt: bool = False          # Trigger system-wide halt
    trigger_cooldown: bool = False   # Trigger per-strategy cooldown


def run_checks(
    risk_hash: dict[str, str],
    opportunity: dict,
    halt_flag: str,
    strategy_state: dict[str, str],
    on_cooldown: bool,
    settings: Settings,
) -> CheckResult:
    """
    Run all pre-trade checks in priority order.

    Args:
        risk_hash:      Current risk:state Redis hash (all string values).
        opportunity:    Deserialized opportunity payload dict.
        halt_flag:      Current value of risk:halt Redis key ("0" or "1").
        strategy_state: Current strategy:state:{name} Redis hash.
        on_cooldown:    Whether risk:cooldown:{strategy} key exists.
        settings:       Risk engine settings (limits).

    Returns:
        CheckResult with approved=True or a single rejection reason.
    """
    passed: list[str] = []

    # ── 1. Kill switch ─────────────────────────────────────────────────────────
    if halt_flag == "1":
        return CheckResult(
            approved=False,
            rejection_reason="kill_switch_active",
            checks_failed=["kill_switch"],
        )
    passed.append("kill_switch")

    # ── 2. Strategy toggle ─────────────────────────────────────────────────────
    if strategy_state.get("enabled", "0") != "1":
        return CheckResult(
            approved=False,
            rejection_reason="strategy_disabled",
            checks_failed=["strategy_enabled"],
        )
    passed.append("strategy_enabled")

    # ── 3. Strategy cooldown ───────────────────────────────────────────────────
    if on_cooldown:
        return CheckResult(
            approved=False,
            rejection_reason="strategy_on_cooldown",
            checks_failed=["strategy_cooldown"],
        )
    passed.append("strategy_cooldown")

    # ── 4. Daily drawdown ──────────────────────────────────────────────────────
    drawdown_pct = float(risk_hash.get("daily_drawdown_pct", 0))
    if drawdown_pct >= settings.RISK_MAX_DAILY_DRAWDOWN_PCT:
        return CheckResult(
            approved=False,
            rejection_reason=f"daily_drawdown_limit_breached:{drawdown_pct:.4f}",
            checks_failed=["daily_drawdown"],
            auto_halt=True,  # System must stop trading immediately
        )
    passed.append("daily_drawdown")

    # ── 5. Max open positions ──────────────────────────────────────────────────
    open_positions = int(risk_hash.get("open_position_count", 0))
    if open_positions >= settings.RISK_MAX_OPEN_POSITIONS:
        return CheckResult(
            approved=False,
            rejection_reason=f"max_open_positions_reached:{open_positions}",
            checks_failed=["max_open_positions"],
        )
    passed.append("max_open_positions")

    # ── 6. Consecutive losses ──────────────────────────────────────────────────
    consecutive_losses = int(risk_hash.get("consecutive_losses", 0))
    if consecutive_losses >= settings.RISK_MAX_CONSECUTIVE_LOSSES:
        return CheckResult(
            approved=False,
            rejection_reason=f"consecutive_loss_limit:{consecutive_losses}",
            checks_failed=["consecutive_losses"],
            trigger_cooldown=True,  # Pause this strategy, not the whole system
        )
    passed.append("consecutive_losses")

    # ── 7. Net edge sanity ─────────────────────────────────────────────────────
    net_edge_bps = float(opportunity.get("net_edge_bps") or 0)
    if net_edge_bps <= 0:
        return CheckResult(
            approved=False,
            rejection_reason=f"net_edge_not_positive:{net_edge_bps}",
            checks_failed=["net_edge_positive"],
        )
    passed.append("net_edge_positive")

    # ── All checks passed ──────────────────────────────────────────────────────
    return CheckResult(approved=True, checks_passed=passed)
