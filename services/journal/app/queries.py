"""
SQL query helpers for the journal service.

All functions use raw SQLAlchemy text() queries for:
  - Predictable query plans on TimescaleDB hypertables
  - Full control over aggregations and filters
  - No ORM overhead on read-heavy paths

All queries are SELECT-only (or UPDATE for reconciliation).
The journal service never INSERTs data — that is the executor's job.

Row serialisation:
  _row_to_dict() converts Rows → plain dicts with:
    UUID  → str
    datetime → ISO-8601 str
    Decimal  → float
"""

import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mezna_shared.db import get_async_session

log = structlog.get_logger()


# ── Row serialisation ─────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict[str, Any]:
    """Convert a SQLAlchemy Row to a JSON-serialisable dict."""
    d = dict(row._mapping)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            d[k] = str(v)
        elif isinstance(v, Decimal):
            d[k] = float(v)
    return d


# ── Trades ────────────────────────────────────────────────────────────────────

_SELECT_TRADES = text("""
    SELECT
        id, opportunity_id, venue, exchange_order_id, client_order_id,
        symbol, side, order_type, quantity, filled_qty, average_fill_price,
        fee, fee_currency, slippage_bps, status, strategy_type,
        paper_mode, rejection_reason, opened_at, filled_at, updated_at
    FROM trades
    WHERE
        (:strategy_type IS NULL OR strategy_type = :strategy_type)
        AND (:venue       IS NULL OR venue = :venue)
        AND (:status      IS NULL OR status = :status)
        AND (:paper_mode  IS NULL OR paper_mode = :paper_mode::boolean)
        AND (:since       IS NULL OR opened_at >= :since::timestamptz)
    ORDER BY opened_at DESC
    LIMIT :limit OFFSET :offset
""")

_COUNT_TRADES = text("""
    SELECT COUNT(*) AS total
    FROM trades
    WHERE
        (:strategy_type IS NULL OR strategy_type = :strategy_type)
        AND (:venue       IS NULL OR venue = :venue)
        AND (:status      IS NULL OR status = :status)
        AND (:paper_mode  IS NULL OR paper_mode = :paper_mode::boolean)
        AND (:since       IS NULL OR opened_at >= :since::timestamptz)
""")


async def list_trades(
    db_engine: AsyncEngine,
    *,
    strategy_type: str | None = None,
    venue: str | None = None,
    status: str | None = None,
    paper_mode: bool | None = None,
    since: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Return paginated list of trades and total count matching filters."""
    params = {
        "strategy_type": strategy_type,
        "venue": venue,
        "status": status,
        "paper_mode": str(paper_mode).lower() if paper_mode is not None else None,
        "since": since,
        "limit": min(limit, 200),
        "offset": offset,
    }
    async with get_async_session(db_engine) as session:
        rows = await session.execute(_SELECT_TRADES, params)
        count_row = await session.execute(_COUNT_TRADES, params)
    trades = [_row_to_dict(r) for r in rows]
    total = count_row.scalar() or 0
    return trades, total


_SELECT_TRADE = text("""
    SELECT
        id, opportunity_id, venue, exchange_order_id, client_order_id,
        symbol, side, order_type, quantity, filled_qty, average_fill_price,
        fee, fee_currency, slippage_bps, status, strategy_type,
        paper_mode, rejection_reason, opened_at, filled_at, updated_at
    FROM trades
    WHERE client_order_id = :client_order_id
    LIMIT 1
""")


async def get_trade(db_engine: AsyncEngine, client_order_id: str) -> dict | None:
    """Fetch a single trade by client_order_id."""
    async with get_async_session(db_engine) as session:
        row = (await session.execute(_SELECT_TRADE, {"client_order_id": client_order_id})).fetchone()
    return _row_to_dict(row) if row else None


_SUMMARY_TRADES = text("""
    SELECT
        strategy_type,
        paper_mode,
        COUNT(*)                                           AS total_trades,
        COUNT(*) FILTER (WHERE status = 'filled')          AS filled,
        COUNT(*) FILTER (WHERE status = 'rejected')        AS rejected,
        COUNT(*) FILTER (WHERE status = 'error')           AS errors,
        COALESCE(SUM(filled_qty * average_fill_price)
            FILTER (WHERE status = 'filled'), 0)           AS total_notional,
        COALESCE(SUM(fee)
            FILTER (WHERE status = 'filled'), 0)           AS total_fees
    FROM trades
    WHERE (:since IS NULL OR opened_at >= :since::timestamptz)
    GROUP BY strategy_type, paper_mode
    ORDER BY strategy_type, paper_mode
""")


async def trades_summary(
    db_engine: AsyncEngine,
    *,
    since: str | None = None,
) -> list[dict]:
    """Aggregate trade stats grouped by strategy_type and paper_mode."""
    async with get_async_session(db_engine) as session:
        rows = await session.execute(_SUMMARY_TRADES, {"since": since})
    return [_row_to_dict(r) for r in rows]


# ── Opportunities ─────────────────────────────────────────────────────────────

_SELECT_OPPORTUNITIES = text("""
    SELECT
        id, strategy_type, venue, source, symbol_primary, symbol_secondary,
        detected_at, latency_profile,
        spread, z_score, funding_rate, expected_return_bps, fee_cost_bps, net_edge_bps,
        ai_score, ai_reasoning, ai_timeout, ai_scored_at,
        risk_approved, risk_rejection_reason, risk_checked_at,
        executed, expired, paper_mode, created_at
    FROM opportunities
    WHERE
        (:strategy_type  IS NULL OR strategy_type = :strategy_type)
        AND (:risk_approved IS NULL OR risk_approved = :risk_approved::boolean)
        AND (:executed     IS NULL OR executed = :executed::boolean)
        AND (:since        IS NULL OR detected_at >= :since::timestamptz)
    ORDER BY detected_at DESC
    LIMIT :limit OFFSET :offset
""")

_COUNT_OPPORTUNITIES = text("""
    SELECT COUNT(*) AS total
    FROM opportunities
    WHERE
        (:strategy_type  IS NULL OR strategy_type = :strategy_type)
        AND (:risk_approved IS NULL OR risk_approved = :risk_approved::boolean)
        AND (:executed     IS NULL OR executed = :executed::boolean)
        AND (:since        IS NULL OR detected_at >= :since::timestamptz)
""")


async def list_opportunities(
    db_engine: AsyncEngine,
    *,
    strategy_type: str | None = None,
    risk_approved: bool | None = None,
    executed: bool | None = None,
    since: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    params = {
        "strategy_type": strategy_type,
        "risk_approved": str(risk_approved).lower() if risk_approved is not None else None,
        "executed": str(executed).lower() if executed is not None else None,
        "since": since,
        "limit": min(limit, 200),
        "offset": offset,
    }
    async with get_async_session(db_engine) as session:
        rows = await session.execute(_SELECT_OPPORTUNITIES, params)
        count_row = await session.execute(_COUNT_OPPORTUNITIES, params)
    return [_row_to_dict(r) for r in rows], (count_row.scalar() or 0)


_SELECT_OPPORTUNITY = text("""
    SELECT
        id, strategy_type, venue, source, symbol_primary, symbol_secondary,
        detected_at, latency_profile,
        spread, z_score, funding_rate, expected_return_bps, fee_cost_bps, net_edge_bps,
        ai_score, ai_reasoning, ai_timeout, ai_scored_at,
        risk_approved, risk_rejection_reason, risk_checked_at,
        executed, expired, paper_mode, created_at
    FROM opportunities
    WHERE id = :opportunity_id::uuid
    LIMIT 1
""")

_TRADES_FOR_OPPORTUNITY = text("""
    SELECT
        client_order_id, venue, symbol, side, status,
        filled_qty, average_fill_price, fee, fee_currency,
        slippage_bps, rejection_reason, opened_at, filled_at
    FROM trades
    WHERE opportunity_id = :opportunity_id::uuid
    ORDER BY opened_at ASC
""")


async def get_opportunity_with_trades(
    db_engine: AsyncEngine, opportunity_id: str
) -> dict | None:
    """Fetch a single opportunity and its linked trades."""
    async with get_async_session(db_engine) as session:
        opp_row = (
            await session.execute(_SELECT_OPPORTUNITY, {"opportunity_id": opportunity_id})
        ).fetchone()
        if not opp_row:
            return None
        trade_rows = (
            await session.execute(_TRADES_FOR_OPPORTUNITY, {"opportunity_id": opportunity_id})
        ).fetchall()

    result = _row_to_dict(opp_row)
    result["trades"] = [_row_to_dict(r) for r in trade_rows]
    return result


# ── P&L ──────────────────────────────────────────────────────────────────────

_DAILY_PNL = text("""
    SELECT
        DATE(filled_at AT TIME ZONE 'UTC')   AS trade_date,
        strategy_type,
        paper_mode,
        COUNT(*)                             AS fill_count,
        COALESCE(SUM(filled_qty * average_fill_price), 0) AS total_notional,
        COALESCE(SUM(fee), 0)                AS total_fees,
        COALESCE(SUM(realized_pnl), 0)       AS realized_pnl
    FROM trades
    WHERE
        status = 'filled'
        AND filled_at IS NOT NULL
        AND filled_at >= NOW() - (:days || ' days')::interval
        AND (:strategy_type IS NULL OR strategy_type = :strategy_type)
    GROUP BY 1, 2, 3
    ORDER BY 1 DESC, 2
""")


async def daily_pnl(
    db_engine: AsyncEngine,
    *,
    days: int = 30,
    strategy_type: str | None = None,
) -> list[dict]:
    """Daily P&L grouped by strategy and paper_mode for the last N days."""
    async with get_async_session(db_engine) as session:
        rows = await session.execute(
            _DAILY_PNL, {"days": days, "strategy_type": strategy_type}
        )
    result = []
    for r in rows:
        d = _row_to_dict(r)
        # trade_date comes back as a date object — convert to string
        if d.get("trade_date") and not isinstance(d["trade_date"], str):
            d["trade_date"] = d["trade_date"].isoformat()
        result.append(d)
    return result


# ── Kelly sizing inputs ───────────────────────────────────────────────────────

_KELLY_STATS = text("""
    SELECT
        COUNT(*)                                                    AS total_filled_trades,
        COUNT(*) FILTER (WHERE realized_pnl > 0)                    AS winning_trades,
        COUNT(*) FILTER (WHERE realized_pnl < 0)                    AS losing_trades,
        COUNT(*) FILTER (WHERE realized_pnl = 0)                    AS breakeven_trades,
        COALESCE(AVG(realized_pnl)
            FILTER (WHERE realized_pnl > 0), 0)                     AS avg_win_usd,
        COALESCE(AVG(ABS(realized_pnl))
            FILTER (WHERE realized_pnl < 0), 0)                     AS avg_loss_usd,
        COALESCE(SUM(realized_pnl), 0)                              AS total_realized_pnl,
        COALESCE(SUM(fee), 0)                                       AS total_fees_usd,
        BOOL_OR(realized_pnl != 0)                                  AS realized_pnl_populated
    FROM trades
    WHERE
        status = 'filled'
        AND filled_at IS NOT NULL
        AND filled_at >= NOW() - (:days || ' days')::interval
        AND (:strategy_type IS NULL OR strategy_type = :strategy_type)
""")


async def kelly_stats(
    db_engine: AsyncEngine,
    *,
    days: int = 30,
    strategy_type: str | None = None,
) -> dict:
    """
    Compute Kelly sizing inputs from filled trades over the last N days.

    Returns:
        total_filled_trades  — number of filled trades in the window
        winning_trades       — trades with realized_pnl > 0
        losing_trades        — trades with realized_pnl < 0
        avg_win_usd          — mean profit per winning trade (USD)
        avg_loss_usd         — mean loss magnitude per losing trade (USD, positive)
        total_realized_pnl   — sum of all realized P&L (USD)
        total_fees_usd       — sum of all fees (USD)
        win_rate             — winning_trades / (winning + losing)
        edge_ratio           — avg_win_usd / avg_loss_usd (0 when no losing trades)
        realized_pnl_populated — False until Phase 7 position-close tracking is live.
                                  When False, avg_win/loss will be 0 — do not use for Kelly.

    NOTE: realized_pnl is always 0 until the position-close logic in Phase 7 is
    implemented.  Callers MUST check realized_pnl_populated before using avg_win_usd
    and avg_loss_usd for Kelly computation.
    """
    params = {"days": days, "strategy_type": strategy_type}
    async with get_async_session(db_engine) as session:
        row = (await session.execute(_KELLY_STATS, params)).fetchone()

    if not row:
        return {
            "total_filled_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "breakeven_trades": 0,
            "avg_win_usd": 0.0,
            "avg_loss_usd": 0.0,
            "total_realized_pnl": 0.0,
            "total_fees_usd": 0.0,
            "win_rate": 0.0,
            "edge_ratio": 0.0,
            "realized_pnl_populated": False,
            "days": days,
            "strategy_type": strategy_type,
        }

    d = _row_to_dict(row)
    total = int(d.get("total_filled_trades", 0) or 0)
    wins = int(d.get("winning_trades", 0) or 0)
    losses = int(d.get("losing_trades", 0) or 0)
    avg_win = float(d.get("avg_win_usd", 0) or 0)
    avg_loss = float(d.get("avg_loss_usd", 0) or 0)

    win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.0
    edge_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

    return {
        "total_filled_trades": total,
        "winning_trades": wins,
        "losing_trades": losses,
        "breakeven_trades": int(d.get("breakeven_trades", 0) or 0),
        "avg_win_usd": round(avg_win, 6),
        "avg_loss_usd": round(avg_loss, 6),
        "total_realized_pnl": round(float(d.get("total_realized_pnl", 0) or 0), 6),
        "total_fees_usd": round(float(d.get("total_fees_usd", 0) or 0), 6),
        "win_rate": round(win_rate, 6),
        "edge_ratio": round(edge_ratio, 6),
        "realized_pnl_populated": bool(d.get("realized_pnl_populated", False)),
        "days": days,
        "strategy_type": strategy_type,
    }


# ── Opportunity funnel ────────────────────────────────────────────────────────

_FUNNEL = text("""
    SELECT
        COUNT(*)                                                      AS detected,
        COUNT(*) FILTER (WHERE ai_scored_at IS NOT NULL)              AS ai_scored,
        COUNT(*) FILTER (WHERE risk_approved = true)                  AS risk_approved,
        COUNT(*) FILTER (WHERE executed = true)                       AS executed,
        COUNT(*) FILTER (WHERE risk_approved = false)                 AS risk_rejected,
        COUNT(*) FILTER (WHERE expired = true)                        AS expired
    FROM opportunities
    WHERE (:since IS NULL OR detected_at >= :since::timestamptz)
""")


async def funnel_stats(
    db_engine: AsyncEngine, *, since: str | None = None
) -> dict:
    """Opportunity funnel: detected → ai_scored → risk_approved → executed."""
    async with get_async_session(db_engine) as session:
        row = (await session.execute(_FUNNEL, {"since": since})).fetchone()
    if not row:
        return {}
    d = _row_to_dict(row)
    detected = int(d.get("detected", 0) or 0)
    risk_approved = int(d.get("risk_approved", 0) or 0)
    executed = int(d.get("executed", 0) or 0)
    d["ai_filter_rate"] = round(int(d.get("ai_scored", 0) or 0) / detected, 4) if detected else 0
    d["risk_approval_rate"] = round(risk_approved / detected, 4) if detected else 0
    d["execution_rate"] = round(executed / risk_approved, 4) if risk_approved else 0
    return d


# ── Audit log ────────────────────────────────────────────────────────────────

_SELECT_AUDIT = text("""
    SELECT
        id, event_type, service, entity_type, entity_id, payload, created_at
    FROM audit_log
    WHERE
        (:event_type IS NULL OR event_type = :event_type)
        AND (:service IS NULL OR service = :service)
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
""")

_COUNT_AUDIT = text("""
    SELECT COUNT(*) AS total
    FROM audit_log
    WHERE
        (:event_type IS NULL OR event_type = :event_type)
        AND (:service IS NULL OR service = :service)
""")


async def list_audit(
    db_engine: AsyncEngine,
    *,
    event_type: str | None = None,
    service: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    params = {
        "event_type": event_type,
        "service": service,
        "limit": min(limit, 200),
        "offset": offset,
    }
    async with get_async_session(db_engine) as session:
        rows = await session.execute(_SELECT_AUDIT, params)
        count_row = await session.execute(_COUNT_AUDIT, params)
    return [_row_to_dict(r) for r in rows], (count_row.scalar() or 0)


_SELECT_RISK_EVENTS = text("""
    SELECT
        id, event_type, strategy_type, venue, symbol,
        trigger_value, threshold_value, description,
        auto_resolved, resolved_at, resolved_by, created_at
    FROM risk_events
    WHERE (:event_type IS NULL OR event_type = :event_type)
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
""")

_COUNT_RISK_EVENTS = text("""
    SELECT COUNT(*) AS total
    FROM risk_events
    WHERE (:event_type IS NULL OR event_type = :event_type)
""")


async def list_risk_events(
    db_engine: AsyncEngine,
    *,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    params = {"event_type": event_type, "limit": min(limit, 200), "offset": offset}
    async with get_async_session(db_engine) as session:
        rows = await session.execute(_SELECT_RISK_EVENTS, params)
        count_row = await session.execute(_COUNT_RISK_EVENTS, params)
    return [_row_to_dict(r) for r in rows], (count_row.scalar() or 0)


# ── Reconciliation helpers ────────────────────────────────────────────────────

_STALE_TRADES = text("""
    SELECT client_order_id, symbol, venue, side, opened_at, strategy_type
    FROM trades
    WHERE
        status IN ('pending', 'open')
        AND opened_at < NOW() - (:stale_minutes || ' minutes')::interval
    ORDER BY opened_at ASC
""")

_MARK_TRADE_ERROR = text("""
    UPDATE trades
    SET
        status = 'error',
        rejection_reason = :reason,
        updated_at = NOW()
    WHERE
        client_order_id = :client_order_id
        AND status IN ('pending', 'open')
""")

_OPEN_POSITION_COUNT = text("""
    SELECT COUNT(DISTINCT opportunity_id)
    FROM trades
    WHERE
        opportunity_id IS NOT NULL
        AND status IN ('pending', 'open', 'partially_filled')
""")


async def find_stale_trades(db_engine: AsyncEngine, stale_minutes: int) -> list[dict]:
    async with get_async_session(db_engine) as session:
        rows = await session.execute(_STALE_TRADES, {"stale_minutes": stale_minutes})
    return [_row_to_dict(r) for r in rows]


async def mark_trade_error(
    db_engine: AsyncEngine, client_order_id: str, reason: str
) -> None:
    async with get_async_session(db_engine) as session:
        await session.execute(
            _MARK_TRADE_ERROR,
            {"client_order_id": client_order_id, "reason": reason},
        )


async def count_open_positions(db_engine: AsyncEngine) -> int:
    async with get_async_session(db_engine) as session:
        row = (await session.execute(_OPEN_POSITION_COUNT)).fetchone()
    return int(row[0]) if row else 0
