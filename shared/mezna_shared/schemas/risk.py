"""Pydantic schemas for Risk state and control — inter-service API contracts."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RiskState(BaseModel):
    """
    Current live risk state as stored in Redis hash risk:state.

    Read by all services before any order is placed.
    Written exclusively by the risk engine.
    """

    daily_pnl_usd: float = 0.0
    daily_drawdown_pct: float = 0.0
    open_position_count: int = 0
    consecutive_losses: int = 0
    trading_halted: bool = False
    halt_reason: str | None = None
    last_updated: datetime | None = None

    # Per-strategy signal counts for today
    funding_arb_signals_today: int = 0
    stat_arb_signals_today: int = 0
    swing_signals_today: int = 0

    @classmethod
    def from_redis_hash(cls, data: dict[str, str]) -> "RiskState":
        """Deserialise from Redis hash (all values are strings in Redis)."""
        return cls(
            daily_pnl_usd=float(data.get("daily_pnl_usd", 0)),
            daily_drawdown_pct=float(data.get("daily_drawdown_pct", 0)),
            open_position_count=int(data.get("open_position_count", 0)),
            consecutive_losses=int(data.get("consecutive_losses", 0)),
            trading_halted=data.get("trading_halted", "0") == "1",
            halt_reason=data.get("halt_reason") or None,
            last_updated=(
                datetime.fromisoformat(data["last_updated"])
                if data.get("last_updated")
                else None
            ),
        )

    def to_redis_hash(self) -> dict[str, str]:
        """Serialise to Redis hash (all values must be strings)."""
        return {
            "daily_pnl_usd": str(self.daily_pnl_usd),
            "daily_drawdown_pct": str(self.daily_drawdown_pct),
            "open_position_count": str(self.open_position_count),
            "consecutive_losses": str(self.consecutive_losses),
            "trading_halted": "1" if self.trading_halted else "0",
            "halt_reason": self.halt_reason or "",
            "last_updated": datetime.utcnow().isoformat(),
        }


class RiskCheckResult(BaseModel):
    """Result of a pre-trade risk check from the risk engine."""

    approved: bool
    rejection_reason: str | None = None
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)
    risk_state_snapshot: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class KillSwitchRequest(BaseModel):
    """Request body for activating the kill switch."""

    reason: str = Field(..., min_length=5, description="Required: why the kill switch is being activated")
    activated_by: str = Field(default="dashboard", description="Who/what activated the switch")


class KillSwitchResetRequest(BaseModel):
    """Request body for resetting the kill switch. Requires explicit confirmation."""

    confirm: bool = Field(
        ...,
        description="Must be true to proceed — prevents accidental resets",
    )
    reason: str = Field(..., min_length=5)
    reset_by: str = Field(default="dashboard")


class StrategyToggleRequest(BaseModel):
    """Request to enable or disable a strategy."""

    strategy_type: str = Field(..., pattern="^(funding_arb|stat_arb|swing)$")
    enabled: bool
    latency_profile: str | None = Field(
        None, pattern="^(relaxed|standard|fast)$"
    )
