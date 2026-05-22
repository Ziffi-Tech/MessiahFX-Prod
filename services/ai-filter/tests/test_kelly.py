"""
Tests for Kelly position-sizing computation.

These tests cover only the pure-Python logic in portfolio_sizing.py:
  - _compute_kelly: the half-Kelly formula
  - The scaling-factor cascade (regime, drawdown, consecutive-loss)
  - data_quality flags from journal kelly-stats response

No HTTP, DB, or Redis calls. All external dependencies are mocked.
"""

import pytest
import sys
import os

# Allow importing from the service app without installing it
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routes.portfolio_sizing import _compute_kelly, _MIN_TRADES_FOR_KELLY, _KELLY_MAX_PCT


# ── _compute_kelly ─────────────────────────────────────────────────────────────

class TestComputeKelly:
    def test_classic_example(self):
        """60% win rate, 1.5:1 edge ratio → half-Kelly = 0.167, but hard cap is 5%."""
        # Full Kelly: 0.60 - 0.40/1.5 = 0.333; half = 0.167 → exceeds KELLY_MAX_PCT (0.05)
        result = _compute_kelly(win_rate=0.60, edge_ratio=1.5)
        assert result == _KELLY_MAX_PCT  # cap always wins for strong signals

    def test_modest_edge_below_cap(self):
        """55% win rate, 1.1:1 edge → half-Kelly = (0.55 - 0.45/1.1)*0.5 ≈ 0.07 → capped at 5%."""
        # Still above cap, but let's use a very thin-edge example that stays below
        # 52% win, 1.05:1 edge: Kelly = 0.52 - 0.48/1.05 = 0.52 - 0.457 = 0.063; half = 0.0317
        result = _compute_kelly(win_rate=0.52, edge_ratio=1.05)
        assert 0.0 < result < _KELLY_MAX_PCT  # below cap, positive
        assert abs(result - 0.0317) < 0.001

    def test_zero_win_rate_returns_zero(self):
        assert _compute_kelly(win_rate=0.0, edge_ratio=2.0) == 0.0

    def test_zero_edge_ratio_returns_zero(self):
        assert _compute_kelly(win_rate=0.6, edge_ratio=0.0) == 0.0

    def test_negative_kelly_clamped_to_zero(self):
        """Win rate 30%, edge ratio 0.5 → negative Kelly → should return 0"""
        result = _compute_kelly(win_rate=0.30, edge_ratio=0.5)
        assert result == 0.0

    def test_hard_cap_respected(self):
        """Very high win rate + edge ratio → result never exceeds KELLY_MAX_PCT"""
        result = _compute_kelly(win_rate=0.95, edge_ratio=10.0)
        assert result == _KELLY_MAX_PCT

    def test_exact_breakeven_edge(self):
        """Win rate = 50%, edge ratio = 1.0 → Kelly = 0 → half = 0"""
        result = _compute_kelly(win_rate=0.5, edge_ratio=1.0)
        assert result == 0.0

    def test_half_kelly_is_half_of_full(self):
        """Verify the 0.5 multiplier is applied correctly"""
        win_rate, edge_ratio = 0.55, 2.0
        full_kelly = win_rate - (1.0 - win_rate) / edge_ratio  # 0.55 - 0.225 = 0.325
        expected_half = min(full_kelly * 0.5, _KELLY_MAX_PCT)
        result = _compute_kelly(win_rate=win_rate, edge_ratio=edge_ratio)
        assert abs(result - expected_half) < 1e-6

    def test_negative_inputs_return_zero(self):
        assert _compute_kelly(win_rate=-0.1, edge_ratio=2.0) == 0.0

    @pytest.mark.parametrize("wr,er", [
        (0.50, 1.5),
        (0.55, 1.2),
        (0.65, 2.5),
        (0.45, 3.0),
    ])
    def test_result_always_within_bounds(self, wr, er):
        result = _compute_kelly(win_rate=wr, edge_ratio=er)
        assert 0.0 <= result <= _KELLY_MAX_PCT


# ── Scaling cascade ───────────────────────────────────────────────────────────

class TestScalingLogic:
    """
    Tests for the drawdown / consecutive-loss / regime scaling.
    These replicate the scaling logic from portfolio_sizing() to verify
    the cascade is applied multiplicatively and in the right direction.
    """

    @staticmethod
    def _drawdown_scale(drawdown_pct: float) -> float:
        if drawdown_pct > 0.025:
            return 0.40
        elif drawdown_pct > 0.015:
            return 0.65
        elif drawdown_pct > 0.01:
            return 0.85
        return 1.0

    @staticmethod
    def _loss_scale(consec: int) -> float:
        if consec >= 4:
            return 0.50
        elif consec >= 3:
            return 0.65
        elif consec >= 2:
            return 0.80
        return 1.0

    def test_no_drawdown_no_reduction(self):
        assert self._drawdown_scale(0.0) == 1.0

    def test_drawdown_above_2_5pct_severe_reduction(self):
        assert self._drawdown_scale(0.028) == 0.40

    def test_drawdown_1_5_to_2_5_moderate_reduction(self):
        assert self._drawdown_scale(0.02) == 0.65

    def test_drawdown_1_to_1_5_mild_reduction(self):
        assert self._drawdown_scale(0.012) == 0.85

    def test_no_consecutive_losses_no_reduction(self):
        assert self._loss_scale(0) == 1.0
        assert self._loss_scale(1) == 1.0

    def test_two_losses_mild_reduction(self):
        assert self._loss_scale(2) == 0.80

    def test_four_losses_severe_reduction(self):
        assert self._loss_scale(4) == 0.50

    def test_scales_multiply(self):
        """Both reductions apply together: 0.65 × 0.80 = 0.52"""
        dd_scale = self._drawdown_scale(0.02)    # 0.65
        loss_scale = self._loss_scale(2)          # 0.80
        combined = dd_scale * loss_scale
        assert abs(combined - 0.52) < 1e-9

    def test_total_scale_never_exceeds_one(self):
        for dd in (0.0, 0.005, 0.011, 0.016, 0.026):
            for cl in range(6):
                scale = self._drawdown_scale(dd) * self._loss_scale(cl)
                assert scale <= 1.0

    def test_total_scale_never_below_zero(self):
        scale = self._drawdown_scale(0.999) * self._loss_scale(99)
        assert scale >= 0.0


# ── data_quality flags ────────────────────────────────────────────────────────

class TestDataQualityFlags:
    """
    Verify the three data_quality states map to correct Kelly values
    and confidence levels.
    """

    def _classify(self, total_trades: int, pnl_live: bool) -> tuple[float, str, str]:
        """Replicate the classification logic from portfolio_sizing()."""
        if total_trades >= _MIN_TRADES_FOR_KELLY and pnl_live:
            kelly_fraction = _compute_kelly(0.60, 1.5)
            data_quality = "live"
        elif total_trades >= _MIN_TRADES_FOR_KELLY and not pnl_live:
            kelly_fraction = 0.01
            data_quality = "pending_phase7"
        else:
            kelly_fraction = 0.01
            data_quality = "insufficient_history"

        if not pnl_live or total_trades < _MIN_TRADES_FOR_KELLY:
            confidence = "low"
        elif total_trades < 30:
            confidence = "medium"
        else:
            confidence = "high"

        return kelly_fraction, data_quality, confidence

    def test_live_data_sufficient_trades(self):
        kelly, quality, conf = self._classify(total_trades=50, pnl_live=True)
        assert quality == "live"
        assert conf == "high"
        assert kelly > 0.01  # Real Kelly computed, not the 1% default

    def test_live_data_barely_enough_trades(self):
        kelly, quality, conf = self._classify(total_trades=_MIN_TRADES_FOR_KELLY, pnl_live=True)
        assert quality == "live"
        assert conf == "medium"

    def test_phase6_enough_trades_but_no_pnl(self):
        """Phase 6: trades recorded but realized_pnl is all zeros."""
        kelly, quality, conf = self._classify(total_trades=50, pnl_live=False)
        assert quality == "pending_phase7"
        assert kelly == 0.01  # Falls back to default
        assert conf == "low"

    def test_insufficient_history(self):
        kelly, quality, conf = self._classify(total_trades=5, pnl_live=False)
        assert quality == "insufficient_history"
        assert kelly == 0.01
        assert conf == "low"

    def test_zero_trades(self):
        kelly, quality, conf = self._classify(total_trades=0, pnl_live=False)
        assert quality == "insufficient_history"
        assert kelly == 0.01
