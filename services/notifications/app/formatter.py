"""
Notification message formatter.

Converts raw event payloads into human-readable alert strings
and Discord embed metadata.

Design goals:
  - Concise (fits in a Telegram notification preview)
  - Context-rich (enough info to act without opening the dashboard)
  - Paper-mode labelled (so paper fills are never confused with real money)
"""

from .channels import NotificationPayload
from .channels.discord import COLOR_GREEN, COLOR_RED, COLOR_ORANGE, COLOR_BLUE


def _mode_tag(paper: bool) -> str:
    return "📋 Paper" if paper else "💰 LIVE"


def _side_icon(side: str | None) -> str:
    if side == "buy":
        return "🟢"
    if side == "sell":
        return "🔴"
    return "⚪"


def _status_icon(status: str | None) -> str:
    return {
        "filled":   "✅",
        "rejected": "⛔",
        "error":    "❌",
        "pending":  "⏳",
    }.get(status or "", "❓")


def format_text(payload: NotificationPayload) -> str:
    """Return a plain-text (HTML-safe) message for Telegram and fallback log."""
    event = payload.event

    if event == "trade.fill":
        icon = _status_icon(payload.status)
        side_icon = _side_icon(payload.side)
        mode = _mode_tag(payload.paper)
        symbol = payload.symbol or "UNKNOWN"
        side = (payload.side or "").upper()
        qty = f"{payload.filled_qty:.6g}" if payload.filled_qty else "?"
        price = f"{payload.avg_price:,.4f}" if payload.avg_price else "?"
        fee = f"{payload.fee:.4f} {payload.fee_currency or ''}" if payload.fee else "?"
        slip = f"{payload.slippage_bps:.1f}bps" if payload.slippage_bps else "?"
        strat = payload.strategy_type or "unknown"

        if payload.status == "filled":
            return (
                f"{icon} <b>FILLED</b> {side_icon} {symbol} | {side} {qty} @ {price}\n"
                f"Fee: {fee} | Slip: {slip} | {strat} | {mode}"
            )
        else:
            reason = payload.reason or payload.status or "unknown"
            return (
                f"{icon} <b>{(payload.status or 'ERROR').upper()}</b> {symbol} {side}\n"
                f"Reason: {reason} | {strat} | {mode}"
            )

    if event == "risk.halt":
        reason = payload.reason or "unspecified"
        return (
            f"🛑 <b>TRADING HALTED</b>\n"
            f"Reason: {reason}\n"
            f"Use the dashboard to reset the kill switch."
        )

    if event == "risk.cooldown":
        strat = payload.strategy_type or "unknown"
        reason = payload.reason or "consecutive losses"
        return (
            f"⏸ <b>COOLDOWN</b> — {strat}\n"
            f"Reason: {reason}"
        )

    if event == "risk.rejected":
        strat = payload.strategy_type or "unknown"
        reason = payload.reason or "limit breach"
        symbol = payload.symbol or ""
        return f"⚠️ Signal rejected | {strat} {symbol}\nReason: {reason}"

    # Generic fallback
    return f"ℹ️ {event}: {payload.reason or str(payload.extra)[:100]}"


def discord_color(payload: NotificationPayload) -> int:
    """Pick a Discord embed colour from the event type and status."""
    if payload.event == "trade.fill":
        if payload.status == "filled":
            return COLOR_GREEN
        return COLOR_ORANGE if payload.status == "rejected" else COLOR_RED
    if payload.event in ("risk.halt",):
        return COLOR_RED
    if payload.event in ("risk.cooldown", "risk.rejected"):
        return COLOR_ORANGE
    return COLOR_BLUE
