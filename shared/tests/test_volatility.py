"""Tests for mezna_shared.volatility."""

import math

from mezna_shared.volatility import (
    returns_from_prices, ewma_vol, garch11_fit, garch11_forecast,
    forecast_vol, annualize, vol_target_multiplier, relative_sizing_multiplier,
)


def test_returns_from_prices():
    r = returns_from_prices([100, 110, 99])
    assert math.isclose(r[0], 0.1)
    assert math.isclose(r[1], (99 - 110) / 110)


def test_ewma_vol_zero_for_flat():
    assert ewma_vol([0.0, 0.0, 0.0]) == 0.0
    assert ewma_vol([]) == 0.0


def test_ewma_vol_positive():
    assert ewma_vol([0.01, -0.01, 0.02, -0.02]) > 0


def test_garch_fit_valid_params():
    # Returns with volatility clustering.
    returns = ([0.005, -0.004, 0.006, -0.005] * 6) + ([0.03, -0.028, 0.031, -0.029] * 6)
    p = garch11_fit(returns)
    assert p is not None
    assert p["omega"] > 0
    assert 0 < p["alpha"] < 1 and 0 < p["beta"] < 1
    assert p["alpha"] + p["beta"] < 1  # stationary
    f = garch11_forecast(returns, p)
    assert f > 0


def test_garch_fit_too_short_is_none():
    assert garch11_fit([0.01, -0.01, 0.02]) is None


def test_forecast_vol_garch_falls_back_to_ewma():
    short = [0.01, -0.01, 0.02]  # too short for garch
    vol, params = forecast_vol(short, method="garch")
    assert params is None
    assert vol == ewma_vol(short)


def test_annualize():
    assert math.isclose(annualize(0.01, 252), 0.01 * math.sqrt(252))


def test_vol_target_multiplier_clamped():
    assert vol_target_multiplier(0.02, 0.01) == 0.5           # halve size
    assert vol_target_multiplier(0.001, 0.01) == 2.0          # clamped at hi
    assert vol_target_multiplier(0.1, 0.01) == 0.25           # clamped at lo
    assert vol_target_multiplier(0.0, 0.01) == 1.0            # no forecast → neutral


def test_relative_multiplier_downsizes_on_spike():
    calm = [0.005, -0.005] * 10
    spike = calm + [0.05, -0.05, 0.05]   # recent vol burst
    assert relative_sizing_multiplier(spike) < 1.0
    assert relative_sizing_multiplier([0.01]) == 1.0          # too short → neutral


def test_relative_multiplier_clamped_range():
    m = relative_sizing_multiplier([0.01, -0.01] * 20)
    assert 0.25 <= m <= 2.0
