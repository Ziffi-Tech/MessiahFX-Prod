"""
Tests for the deterministic pre-trade risk gate (risk.app.checker.run_checks).

run_checks is pure (plain dicts + Settings), so these exercise every gate in
priority order — most importantly the kill switch, which must block ALL activity.
"""

from app.checker import run_checks
from app.config import settings, Settings

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


# ── Phase 4 capital controls (caps default OFF; constructed Settings turn them on) ──

def _caps(**overrides):
    """A Settings with capital-control caps set (DATABASE_URL comes from env)."""
    return Settings(**overrides)


def _run(s, *, risk=None, gross=0.0, strat_exp=0.0, notional=0.0):
    return run_checks(
        risk_hash=risk if risk is not None else dict(_RISK_CLEAN),
        opportunity=dict(_OPP),
        halt_flag="0",
        strategy_state=dict(_ENABLED),
        on_cooldown=False,
        settings=s,
        gross_exposure_usd=gross,
        strategy_exposure_usd=strat_exp,
        new_notional_usd=notional,
    )


def test_caps_off_by_default_ignores_exposure():
    # Global settings have caps = 0 → huge exposure is irrelevant.
    r = run_checks(
        risk_hash=dict(_RISK_CLEAN), opportunity=dict(_OPP), halt_flag="0",
        strategy_state=dict(_ENABLED), on_cooldown=False, settings=settings,
        gross_exposure_usd=1_000_000, strategy_exposure_usd=1_000_000, new_notional_usd=50_000,
    )
    assert r.approved


def test_gross_exposure_cap_rejects_over():
    s = _caps(RISK_MAX_GROSS_EXPOSURE_USD=1000)
    r = _run(s, gross=950, notional=100)   # 1050 > 1000
    assert not r.approved and r.rejection_reason.startswith("gross_exposure_cap")
    assert "gross_exposure" in r.checks_failed


def test_gross_exposure_within_cap_ok():
    s = _caps(RISK_MAX_GROSS_EXPOSURE_USD=1000)
    r = _run(s, gross=800, notional=100)   # 900 <= 1000
    assert r.approved


def test_strategy_exposure_cap_rejects_over():
    s = _caps(RISK_MAX_STRATEGY_EXPOSURE_USD=500)
    r = _run(s, strat_exp=450, notional=100)  # 550 > 500
    assert not r.approved and r.rejection_reason.startswith("strategy_exposure_cap")


def test_daily_loss_limit_auto_halts():
    s = _caps(RISK_DAILY_LOSS_LIMIT_USD=200)
    r = _run(s, risk={**_RISK_CLEAN, "daily_pnl_usd": "-250"})
    assert not r.approved and r.auto_halt is True
    assert r.rejection_reason.startswith("daily_loss_limit_usd")


def test_daily_loss_within_limit_ok():
    s = _caps(RISK_DAILY_LOSS_LIMIT_USD=200)
    r = _run(s, risk={**_RISK_CLEAN, "daily_pnl_usd": "-150"})
    assert r.approved
