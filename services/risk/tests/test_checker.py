"""
Tests for the deterministic pre-trade risk gate (risk.app.checker.run_checks).

run_checks is pure (plain dicts + Settings), so these exercise every gate in
priority order — most importantly the kill switch, which must block ALL activity.
"""

from app.checker import run_checks
from app.config import settings

# A clean, approvable context; individual fields are overridden per test.
_OPP = {"strategy_type": "swing", "net_edge_bps": 5.0}
_ENABLED = {"enabled": "1"}
_RISK_CLEAN = {"daily_drawdown_pct": "0", "open_position_count": "0", "consecutive_losses": "0"}


def _check(*, halt="0", strat=None, cooldown=False, risk=None, opp=None):
    return run_checks(
        risk_hash=risk if risk is not None else dict(_RISK_CLEAN),
        opportunity=opp if opp is not None else dict(_OPP),
        halt_flag=halt,
        strategy_state=strat if strat is not None else dict(_ENABLED),
        on_cooldown=cooldown,
        settings=settings,
    )


def test_kill_switch_blocks_everything():
    r = _check(halt="1")
    assert not r.approved
    assert r.rejection_reason == "kill_switch_active"
    assert "kill_switch" in r.checks_failed


def test_clean_opportunity_approved():
    r = _check()
    assert r.approved
    assert "kill_switch" in r.checks_passed
    assert "net_edge_positive" in r.checks_passed


def test_strategy_disabled_rejected():
    r = _check(strat={"enabled": "0"})
    assert not r.approved and r.rejection_reason == "strategy_disabled"


def test_cooldown_rejected():
    r = _check(cooldown=True)
    assert not r.approved and r.rejection_reason == "strategy_on_cooldown"


def test_daily_drawdown_auto_halts():
    r = _check(risk={**_RISK_CLEAN, "daily_drawdown_pct": str(settings.RISK_MAX_DAILY_DRAWDOWN_PCT)})
    assert not r.approved and r.auto_halt is True
    assert r.rejection_reason.startswith("daily_drawdown_limit_breached")


def test_max_open_positions_rejected():
    r = _check(risk={**_RISK_CLEAN, "open_position_count": str(settings.RISK_MAX_OPEN_POSITIONS)})
    assert not r.approved and r.rejection_reason.startswith("max_open_positions_reached")


def test_consecutive_losses_triggers_cooldown():
    r = _check(risk={**_RISK_CLEAN, "consecutive_losses": str(settings.RISK_MAX_CONSECUTIVE_LOSSES)})
    assert not r.approved and r.trigger_cooldown is True


def test_non_positive_net_edge_rejected():
    r = _check(opp={"strategy_type": "swing", "net_edge_bps": 0.0})
    assert not r.approved and r.rejection_reason.startswith("net_edge_not_positive")
