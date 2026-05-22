"""Notification payload schema and base types."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NotificationPayload:
    """
    Normalised notification event consumed from notifications:queue.

    All producers (executor, risk, gateway) push JSON-encoded dicts that
    map to this structure. Unknown fields are captured in `extra`.

    Event types:
        trade.fill      — order filled, rejected, or errored
        risk.halt       — kill switch activated (auto or manual)
        risk.cooldown   — strategy cooldown triggered
        risk.rejected   — signal rejected by risk checks
        system.startup  — service started (optional operational ping)
    """
    event: str
    timestamp: str
    # Trade fill fields
    symbol: str | None = None
    side: str | None = None
    status: str | None = None
    filled_qty: float | None = None
    avg_price: float | None = None
    fee: float | None = None
    fee_currency: str | None = None
    slippage_bps: float | None = None
    strategy_type: str | None = None
    paper: bool = True
    opportunity_id: str | None = None
    # Risk fields
    reason: str | None = None
    trigger_value: float | None = None
    threshold_value: float | None = None
    # Passthrough
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "NotificationPayload":
        known = {
            "event", "timestamp", "symbol", "side", "status",
            "filled_qty", "avg_price", "fee", "fee_currency",
            "slippage_bps", "strategy_type", "paper", "opportunity_id",
            "reason", "trigger_value", "threshold_value",
        }
        kwargs = {k: v for k, v in data.items() if k in known}
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(**kwargs, extra=extra)
