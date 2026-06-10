"""Tests for mezna_shared.order_state."""

from mezna_shared import order_state as os


def test_terminal_states_have_no_transitions():
    for state in os.TERMINAL_STATES:
        assert os.is_terminal(state)
        assert not os.is_valid_transition(state, os.OPEN)
        assert not os.is_valid_transition(state, os.FILLED)


def test_valid_transitions():
    assert os.is_valid_transition(os.PENDING, os.FILLED)
    assert os.is_valid_transition(os.PENDING, os.OPEN)
    assert os.is_valid_transition(os.OPEN, os.PARTIALLY_FILLED)
    assert os.is_valid_transition(os.PARTIALLY_FILLED, os.FILLED)


def test_invalid_transitions():
    assert not os.is_valid_transition(os.FILLED, os.PENDING)
    assert not os.is_valid_transition(os.OPEN, os.PENDING)
    assert not os.is_valid_transition(os.PARTIALLY_FILLED, os.OPEN)


def test_normalize_known_and_aliases():
    assert os.normalize_status("FILLED") == os.FILLED
    assert os.normalize_status("partial") == os.PARTIALLY_FILLED
    assert os.normalize_status("canceled") == os.CANCELLED
    assert os.normalize_status("new") == os.PENDING


def test_normalize_unknown_is_none():
    assert os.normalize_status("banana") is None
    assert os.normalize_status("") is None
    assert os.normalize_status(None) is None
