"""
Order lifecycle state machine.

A single source of truth for valid order states and the transitions between them.
Today's flow is synchronous market orders (pending → filled/rejected in one step);
this models the full lifecycle so limit/async orders (partial fills, cancels) can
be added without ad-hoc status strings drifting across services.
"""

from __future__ import annotations

# States
PENDING = "pending"
OPEN = "open"
PARTIALLY_FILLED = "partially_filled"
FILLED = "filled"
CANCELLED = "cancelled"
REJECTED = "rejected"
ERROR = "error"
EXPIRED = "expired"

ALL_STATES = frozenset({
    PENDING, OPEN, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, ERROR, EXPIRED,
})

# Terminal states have no outgoing transitions.
TERMINAL_STATES = frozenset({FILLED, CANCELLED, REJECTED, ERROR, EXPIRED})

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({OPEN, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, ERROR, EXPIRED}),
    OPEN: frozenset({PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, ERROR, EXPIRED}),
    PARTIALLY_FILLED: frozenset({PARTIALLY_FILLED, FILLED, CANCELLED, ERROR, EXPIRED}),
}

# Aliases / common synonyms normalised onto canonical states.
_ALIASES = {
    "new": PENDING,
    "accepted": OPEN,
    "partial": PARTIALLY_FILLED,
    "partially_filled": PARTIALLY_FILLED,
    "closed": FILLED,
    "canceled": CANCELLED,
    "cancelled": CANCELLED,
}


def normalize_status(status: str | None) -> str | None:
    """Map a raw status onto a canonical state, or None if unrecognised."""
    if not status:
        return None
    s = status.strip().lower()
    if s in ALL_STATES:
        return s
    return _ALIASES.get(s)


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES


def is_valid_transition(from_state: str, to_state: str) -> bool:
    """True if from_state → to_state is allowed (terminal states allow nothing)."""
    return to_state in VALID_TRANSITIONS.get(from_state, frozenset())
