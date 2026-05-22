"""Pydantic v2 schemas for inter-service API contracts."""

from .opportunity import OpportunityCreate, OpportunityRead, OpportunityUpdate
from .trade import TradeCreate, TradeRead, TradeStatusUpdate
from .risk import RiskState, RiskCheckResult, KillSwitchRequest

__all__ = [
    "OpportunityCreate",
    "OpportunityRead",
    "OpportunityUpdate",
    "TradeCreate",
    "TradeRead",
    "TradeStatusUpdate",
    "RiskState",
    "RiskCheckResult",
    "KillSwitchRequest",
]
