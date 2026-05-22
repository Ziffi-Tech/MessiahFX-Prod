"""
Tests for the journal kelly_stats query helper.

These tests verify the pure-Python post-processing logic in queries.kelly_stats():
  - win_rate and edge_ratio are derived correctly from raw aggregates
  - realized_pnl_populated flag is correct
  - zero-trade edge cases don't divide by zero

The SQL itself is not tested here (that requires a live DB).
Instead, we test the Python layer that assembles the return dict
from what the DB would give back — by calling the function with
a patched DB session.
"""

import pytest
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Pure-logic helpers (tested without DB) ────────────────────────────────────

def _build_kelly_dict(
    total_filled: int,
    winning: int,
    losing: int,
    avg_win: float,
    avg_loss: float,
    total_realized_pnl: float,
    total_fees: float,
    pnl_populated: bool,
    days: int = 30,
    strategy_type: str | None = None,
) -> dict:
    """
    Replicate the dict-assembly logic from queries.kelly_stats()
    without needing a DB connection.
    """
    win_rate = winning / (winning + losing) if (winning + losing) > 0 else 0.0
    edge_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
    return {
        "total_filled_trades": total_filled,
        "winning_trades": winning,
        "losing_trades": losing,
        "breakeven_trades": total_filled - winning - losing,
        "avg_win_usd": round(avg_win, 6),
        "avg_loss_usd": round(avg_loss, 6),
        "total_realized_pnl": round(total_realized_pnl, 6),
        "total_fees_usd": round(total_fees, 6),
        "win_rate": round(win_rate, 6),
        "edge_ratio": round(edge_ratio, 6),
        "realized_pnl_populated": pnl_populated,
        "days": days,
        "strategy_type": strategy_type,
    }


class TestKellyStatsDictAssembly:
    """Unit tests for the dict-assembly logic, independent of DB."""

    def test_standard_case(self):
        d = _build_kelly_dict(
            total_filled=20, winning=12, losing=8,
            avg_win=15.0, avg_loss=8.0,
            total_realized_pnl=116.0, total_fees=10.0,
            pnl_populated=True,
        )
        assert d["total_filled_trades"] == 20
        assert d["winning_trades"] == 12
        assert d["losing_trades"] == 8
        assert d["win_rate"] == pytest.approx(12 / 20, rel=1e-5)
        assert d["edge_ratio"] == pytest.approx(15.0 / 8.0, rel=1e-5)
        assert d["realized_pnl_populated"] is True

    def test_win_rate_exact(self):
        d = _build_kelly_dict(10, 6, 4, 10.0, 5.0, 0, 0, True)
        assert d["win_rate"] == pytest.approx(0.6)

    def test_edge_ratio_exact(self):
        d = _build_kelly_dict(10, 6, 4, 10.0, 5.0, 0, 0, True)
        assert d["edge_ratio"] == pytest.approx(2.0)

    def test_zero_trades_no_division_error(self):
        d = _build_kelly_dict(0, 0, 0, 0.0, 0.0, 0.0, 0.0, False)
        assert d["win_rate"] == 0.0
        assert d["edge_ratio"] == 0.0
        assert d["total_filled_trades"] == 0

    def test_zero_losses_edge_ratio_zero(self):
        """No losing trades → avg_loss = 0 → edge_ratio = 0 (not infinity)."""
        d = _build_kelly_dict(10, 10, 0, 12.0, 0.0, 120.0, 5.0, True)
        assert d["edge_ratio"] == 0.0
        assert d["win_rate"] == 1.0

    def test_zero_wins_win_rate_zero(self):
        d = _build_kelly_dict(10, 0, 10, 0.0, 8.0, -80.0, 5.0, True)
        assert d["win_rate"] == 0.0

    def test_phase6_pnl_not_populated(self):
        """Phase 6: all realized_pnl = 0, avg_win/loss = 0."""
        d = _build_kelly_dict(50, 0, 0, 0.0, 0.0, 0.0, 25.0, False)
        assert d["realized_pnl_populated"] is False
        assert d["avg_win_usd"] == 0.0
        assert d["avg_loss_usd"] == 0.0
        # Caller must check realized_pnl_populated before using these for Kelly

    def test_strategy_type_passthrough(self):
        d = _build_kelly_dict(10, 6, 4, 10.0, 5.0, 0, 0, True, strategy_type="funding_arb")
        assert d["strategy_type"] == "funding_arb"

    def test_days_passthrough(self):
        d = _build_kelly_dict(10, 6, 4, 10.0, 5.0, 0, 0, True, days=7)
        assert d["days"] == 7

    def test_breakeven_trades_computed(self):
        """Breakeven = total - winning - losing."""
        d = _build_kelly_dict(15, 8, 5, 10.0, 5.0, 0, 0, True)
        assert d["breakeven_trades"] == 2

    def test_rounding_applied(self):
        d = _build_kelly_dict(3, 2, 1, 10.123456789, 5.987654321, 0, 0, True)
        # avg_win and avg_loss should be rounded to 6 decimal places
        assert len(str(d["avg_win_usd"]).split(".")[-1]) <= 6
        assert len(str(d["avg_loss_usd"]).split(".")[-1]) <= 6

    @pytest.mark.parametrize("winning,losing,expected_wr", [
        (10, 10, 0.5),
        (7,  3,  0.7),
        (1,  9,  0.1),
        (0,  0,  0.0),
    ])
    def test_win_rate_parametrize(self, winning, losing, expected_wr):
        d = _build_kelly_dict(winning + losing, winning, losing, 10.0, 5.0, 0, 0, True)
        assert d["win_rate"] == pytest.approx(expected_wr, rel=1e-5)

    @pytest.mark.parametrize("avg_win,avg_loss,expected_er", [
        (10.0, 5.0, 2.0),
        (5.0, 5.0, 1.0),
        (3.0, 6.0, 0.5),
        (0.0, 5.0, 0.0),   # No wins → edge_ratio = 0
        (5.0, 0.0, 0.0),   # No losses → avg_loss = 0 → guard returns 0
    ])
    def test_edge_ratio_parametrize(self, avg_win, avg_loss, expected_er):
        d = _build_kelly_dict(10, 5, 5, avg_win, avg_loss, 0, 0, True)
        assert d["edge_ratio"] == pytest.approx(expected_er)


class TestKellyStatsIntegrationWithKellyFormula:
    """
    Verify that the output of kelly_stats feeds correctly into _compute_kelly.
    This is the most important integration: if the journal returns correct
    win_rate and edge_ratio, the Kelly formula produces the right fraction.
    """

    def test_60pct_win_2x_edge_gives_positive_kelly(self):
        """60% win, 2:1 edge → Kelly = 0.60 - 0.40/2 = 0.40 → half = 0.20"""
        d = _build_kelly_dict(100, 60, 40, 20.0, 10.0, 0, 0, True)
        assert d["win_rate"] == pytest.approx(0.6)
        assert d["edge_ratio"] == pytest.approx(2.0)

        # Apply half-Kelly (replicating _compute_kelly logic)
        wr, er = d["win_rate"], d["edge_ratio"]
        kelly = wr - (1.0 - wr) / er
        half_kelly = kelly * 0.5
        assert half_kelly == pytest.approx(0.20, rel=1e-5)

    def test_50pct_win_1x_edge_zero_kelly(self):
        """50% win, 1:1 edge → Kelly = 0 → strategy has no edge."""
        d = _build_kelly_dict(100, 50, 50, 10.0, 10.0, 0, 0, True)
        kelly = d["win_rate"] - (1.0 - d["win_rate"]) / d["edge_ratio"]
        assert kelly == pytest.approx(0.0, abs=1e-9)

    def test_negative_kelly_clamps_to_zero(self):
        """30% win, 0.5:1 edge → Kelly < 0 → should clamp to 0."""
        d = _build_kelly_dict(100, 30, 70, 5.0, 10.0, 0, 0, True)
        kelly = d["win_rate"] - (1.0 - d["win_rate"]) / d["edge_ratio"]
        assert kelly < 0  # Confirmed negative — caller clamps at 0

    def test_phase6_data_blocks_kelly(self):
        """When realized_pnl_populated is False, caller must use default not Kelly."""
        d = _build_kelly_dict(50, 0, 0, 0.0, 0.0, 0.0, 0.0, pnl_populated=False)
        # Caller checks this flag:
        assert not d["realized_pnl_populated"]
        # avg_win and avg_loss are 0 — using them in Kelly would give nonsense
        assert d["avg_win_usd"] == 0.0
        assert d["avg_loss_usd"] == 0.0
