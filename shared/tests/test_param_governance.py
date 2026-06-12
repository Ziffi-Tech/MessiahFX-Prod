"""Tests for mezna_shared.param_governance."""

from mezna_shared.param_governance import (
    param_hash, diff_params, has_drift, drift_report, next_version, canonical_json,
)


def test_hash_is_order_independent():
    a = {"z_score_entry": 2.0, "lookback_hours": 24}
    b = {"lookback_hours": 24, "z_score_entry": 2.0}
    assert param_hash(a) == param_hash(b)


def test_hash_changes_with_value():
    assert param_hash({"z": 2.0}) != param_hash({"z": 2.5})


def test_diff_added_removed_changed():
    old = {"a": 1, "b": 2, "c": 3}
    new = {"a": 1, "b": 5, "d": 9}
    d = diff_params(old, new)
    assert d["added"] == {"d": 9}
    assert d["removed"] == {"c": 3}
    assert d["changed"] == {"b": {"old": 2, "new": 5}}


def test_no_drift_when_equal():
    p = {"z_score_entry": 2.0, "z_score_exit": 0.5}
    assert has_drift(p, dict(p)) is False


def test_drift_when_different():
    assert has_drift({"z": 2.0}, {"z": 3.0}) is True


def test_drift_report():
    live = {"z_score_entry": 2.5, "lookback_hours": 24}
    ref = {"z_score_entry": 2.0, "lookback_hours": 24}
    r = drift_report(live, ref)
    assert r["has_drift"] is True
    assert r["diff"]["changed"]["z_score_entry"] == {"old": 2.0, "new": 2.5}
    assert r["live_hash"] != r["reference_hash"]


def test_next_version():
    assert next_version(3) == 4
    assert next_version("7") == 8
    assert next_version(None) == 1
    assert next_version("garbage") == 1


def test_canonical_json_handles_empty():
    assert canonical_json(None) == "{}"
    assert canonical_json({}) == "{}"
