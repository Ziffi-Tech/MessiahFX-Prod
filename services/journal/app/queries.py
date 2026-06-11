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
        AND filled_at >= NOW() - make_interval(days => :days)
        AND (CAST(:strategy_type AS text) IS NULL OR strategy_type = CAST(:strategy_type AS text))
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


def summary_curve_metrics(rows: list[dict]) -> tuple[float, float | None]:
    """
    Derive (max_drawdown_pct, sharpe_ratio) from daily_pnl rows.

    Pure function — no DB. Aggregates the per-(date,strategy,paper) realized_pnl
    rows into one daily P&L series, then:
      - max_drawdown_pct: worst peak-to-trough of the cumulative realized-P&L
        curve, as a percentage of the running peak (0 while the curve only rises
        or the peak is non-positive).
      - sharpe_ratio: annualised Sharpe of the daily realized-P&L series
        (mean/stdev × √252). None until there are ≥2 days with non-zero variance.

    Both are honest approximations for the summary card: exact trade-count stats
    come from kelly_stats; these add a shape-of-equity read without a capital base.
    """
    import math
    from collections import defaultdict

    by_date: dict[str, float] = defaultdict(float)
    for r in rows:
        date = str(r.get("trade_date"))
        by_date[date] += float(r.get("realized_pnl", 0) or 0)

    if not by_date:
        return 0.0, None

    daily = [by_date[d] for d in sorted(by_date)]

    cum = peak = max_dd_pct = 0.0
    for x in daily:
        cum += x
        peak = max(peak, cum)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, (peak - cum) / peak * 100.0)

    sharpe: float | None = None
    if len(daily) >= 2:
        mean = sum(daily) / len(daily)
        var = sum((x - mean) ** 2 for x in daily) / (len(daily) - 1)
        std = math.sqrt(var)
        if std > 0:
            sharpe = round(mean / std * math.sqrt(252), 4)

    return round(max_dd_pct, 4), sharpe


# ── Performance metrics (per-strategy review + TCA) ────────────────────────────

def _daily_series(rows: list[dict]) -> list[float]:
    """Collapse daily_pnl rows into one ascending daily realized-P&L series."""
    from collections import defaultdict
    by_date: dict[str, float] = defaultdict(float)
    for r in rows:
        by_date[str(r.get("trade_date"))] += float(r.get("realized_pnl", 0) or 0)
    return [by_date[d] for d in sorted(by_date)]


def _max_drawdown_pct(daily: list[float]) -> float:
    cum = peak = mdd = 0.0
    for x in daily:
        cum += x
        peak = max(peak, cum)
        if peak > 0:
            mdd = max(mdd, (peak - cum) / peak * 100.0)
    return round(mdd, 4)


def _sharpe(daily: list[float]) -> float | None:
    import math
    if len(daily) < 2:
        return None
    mean = sum(daily) / len(daily)
    var = sum((x - mean) ** 2 for x in daily) / (len(daily) - 1)
    std = math.sqrt(var)
    return round(mean / std * math.sqrt(252), 4) if std > 0 else None


def _sortino(daily: list[float]) -> float | None:
    """Annualised Sortino — mean / downside deviation × √252. None if no downside."""
    import math
    if len(daily) < 2:
        return None
    mean = sum(daily) / len(daily)
    downside = sum(min(0.0, x) ** 2 for x in daily) / len(daily)
    dd = math.sqrt(downside)
    return round(mean / dd * math.sqrt(252), 4) if dd > 0 else None


def curve_performance(rows: list[dict]) -> dict:
    """Pure: max_drawdown_pct + Sharpe + Sortino from daily_pnl rows."""
    daily = _daily_series(rows)
    return {
        "max_drawdown_pct": _max_drawdown_pct(daily),
        "sharpe_ratio": _sharpe(daily),
        "sortino_ratio": _sortino(daily),
    }


def cost_bps(cost: float, notional: float) -> float:
    """Cost (fee or slippage notional) as basis points of traded notional."""
    return round(cost / notional * 10_000.0, 4) if notional > 0 else 0.0


def align_daily_returns(rows: list[dict]) -> dict[str, list[float]]:
    """
    Build DATE-ALIGNED per-strategy daily realised-P&L series from daily_pnl rows.

    All strategies share one sorted date axis; a strategy that didn't trade on a
    date gets 0 there — so the series are equal-length and comparable for the
    capital-allocation covariance.
    """
    dates = sorted({str(r.get("trade_date")) for r in rows})
    index = {d: i for i, d in enumerate(dates)}
    series: dict[str, list[float]] = {}
    for r in rows:
        strat = r.get("strategy_type") or "unknown"
        series.setdefault(strat, [0.0] * len(dates))
        series[strat][index[str(r.get("trade_date"))]] += float(r.get("realized_pnl", 0) or 0)
    return series


_PERF_BY_STRATEGY = text("""
    SELECT
        strategy_type,
        COUNT(*)                                                    AS filled_trades,
        COUNT(*) FILTER (WHERE realized_pnl > 0)                    AS winning_trades,
        COUNT(*) FILTER (WHERE realized_pnl < 0)                    AS losing_trades,
        COALESCE(AVG(realized_pnl) FILTER (WHERE realized_pnl > 0), 0)      AS avg_win_usd,
        COALESCE(AVG(ABS(realized_pnl)) FILTER (WHERE realized_pnl < 0), 0) AS avg_loss_usd,
        COALESCE(SUM(realized_pnl), 0)                              AS total_realized_pnl,
        COALESCE(SUM(fee), 0)                                       AS total_fees_usd
    FROM trades
    WHERE status = 'filled' AND filled_at IS NOT NULL
      AND filled_at >= NOW() - make_interval(days => :days)
    GROUP BY strategy_type
""")


async def performance_by_strategy(db_engine: AsyncEngine, *, days: int = 30) -> dict:
    """
    Per-strategy performance review for the paper run: trade-level win/loss stats
    plus equity-shape metrics (Sharpe, Sortino, max drawdown) — judge each strategy
    'good', not just green, and cut/retune the laggards.
    """
    from collections import defaultdict

    async with get_async_session(db_engine) as session:
        stat_rows = (await session.execute(_PERF_BY_STRATEGY, {"days": days})).fetchall()

    daily_rows = await daily_pnl(db_engine, days=days)
    daily_by_strategy: dict[str, list[dict]] = defaultdict(list)
    for r in daily_rows:
        daily_by_strategy[r.get("strategy_type")].append(r)

    strategies: list[dict] = []
    for row in stat_rows:
        d = _row_to_dict(row)
        strat = d.get("strategy_type")
        wins = int(d.get("winning_trades", 0) or 0)
        losses = int(d.get("losing_trades", 0) or 0)
        avg_win = float(d.get("avg_win_usd", 0) or 0)
        avg_loss = float(d.get("avg_loss_usd", 0) or 0)
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.0
        gross_profit = avg_win * wins
        gross_loss = avg_loss * losses
        profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else None

        strategies.append({
            "strategy_type": strat,
            "filled_trades": int(d.get("filled_trades", 0) or 0),
            "winning_trades": wins,
            "losing_trades": losses,
            "win_rate": round(win_rate, 6),
            "average_win": round(avg_win, 6),
            "average_loss": round(avg_loss, 6),
            "profit_factor": profit_factor,
            "realized_pnl": round(float(d.get("total_realized_pnl", 0) or 0), 6),
            "total_fees": round(float(d.get("total_fees_usd", 0) or 0), 6),
            **curve_performance(daily_by_strategy.get(strat, [])),
        })

    strategies.sort(key=lambda s: s["realized_pnl"], reverse=True)
    return {"days": days, "strategies": strategies}


_TCA = text("""
    SELECT
        strategy_type,
        venue,
        COUNT(*)                                          AS fills,
        COALESCE(SUM(filled_qty * average_fill_price), 0) AS notional,
        COALESCE(SUM(fee), 0)                             AS total_fees,
        COALESCE(AVG(slippage_bps), 0)                    AS avg_slippage_bps
    FROM trades
    WHERE status = 'filled' AND filled_at IS NOT NULL
      AND filled_at >= NOW() - make_interval(days => :days)
    GROUP BY strategy_type, venue
    ORDER BY notional DESC
""")


async def tca_report(db_engine: AsyncEngine, *, days: int = 30) -> dict:
    """
    Transaction-cost analysis: realised fees + slippage per (strategy, venue), with
    fee in basis points of notional — compare against the backtest's fee/slippage
    assumptions before trusting its edge.
    """
    async with get_async_session(db_engine) as session:
        rows = (await session.execute(_TCA, {"days": days})).fetchall()

    out: list[dict] = []
    tot_notional = tot_fees = 0.0
    for row in rows:
        d = _row_to_dict(row)
        notional = float(d.get("notional", 0) or 0)
        fees = float(d.get("total_fees", 0) or 0)
        tot_notional += notional
        tot_fees += fees
        out.append({
            "strategy_type": d.get("strategy_type"),
            "venue": d.get("venue"),
            "fills": int(d.get("fills", 0) or 0),
            "notional": round(notional, 2),
            "total_fees": round(fees, 6),
            "fee_bps": cost_bps(fees, notional),
            "avg_slippage_bps": round(float(d.get("avg_slippage_bps", 0) or 0), 4),
        })

    return {
        "days": days,
        "rows": out,
        "totals": {
            "notional": round(tot_notional, 2),
            "total_fees": round(tot_fees, 6),
            "fee_bps": cost_bps(tot_fees, tot_notional),
        },
    }


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
        AND filled_at >= NOW() - make_interval(days => :days)
        AND (CAST(:strategy_type AS text) IS NULL OR strategy_type = CAST(:strategy_type AS text))
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
        realized_pnl_populated — True once any position has been closed (realized P&L
                                  is now tracked per fill via average-cost accounting,
                                  migration 003). False only before the first close,
                                  when avg_win/loss are still 0 — do not use for Kelly.

    Realized P&L is net of fees and populated by the executor as positions are
    reduced/closed. Callers SHOULD still check realized_pnl_populated before using
    avg_win_usd / avg_loss_usd, since both are 0 until the first round trip closes.
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


# ── Go-live readiness ─────────────────────────────────────────────────────────

_READINESS_TRADES = text("""
    SELECT
        COUNT(*) FILTER (WHERE status = 'filled')                                   AS filled_trades,
        COUNT(*) FILTER (WHERE status = 'filled' AND realized_pnl IS NOT NULL
                         AND realized_pnl <> 0)                                     AS closed_trades,
        COUNT(*) FILTER (WHERE status = 'filled' AND paper_mode = false)            AS live_fills,
        MIN(filled_at) FILTER (WHERE status = 'filled')                             AS first_fill
    FROM trades
""")

_READINESS_AUDIT = text("""
    SELECT
        COUNT(*) FILTER (WHERE event_type = 'kill_switch.activated')                AS kill_tests,
        COUNT(*) FILTER (WHERE event_type = 'bot.started')                          AS bot_starts,
        MIN(created_at) FILTER (WHERE event_type IN ('bot.started', 'kill_switch.activated')) AS first_activity
    FROM audit_log
""")

_READINESS_RISK = text("""
    SELECT COUNT(*) AS breach_events
    FROM risk_events
    WHERE event_type ILIKE '%drawdown%' OR event_type ILIKE '%limit%' OR event_type ILIKE '%breach%'
""")


async def go_live_readiness(
    db_engine: AsyncEngine,
    *,
    min_paper_days: int = 28,
    min_trades: int = 50,
) -> dict:
    """
    Evaluate the go-live gate from real activity and return a pass/fail checklist.

    Critical criteria (all must pass for ``ready``):
      - paper_duration       : ≥ min_paper_days days of paper activity
      - kill_switch_tested   : at least one kill-switch activation on record
      - sufficient_trades    : ≥ min_trades filled trades (statistical meaning)
      - round_trips_closed   : ≥ 1 position closed (realized P&L is populated)
      - still_paper          : zero live fills (no accidental live trading)

    Advisory (surfaced, not gating):
      - risk_breaches        : count of drawdown/limit breach risk events
    """
    async with get_async_session(db_engine) as session:
        t = _row_to_dict((await session.execute(_READINESS_TRADES)).fetchone())
        a = _row_to_dict((await session.execute(_READINESS_AUDIT)).fetchone())
        r = _row_to_dict((await session.execute(_READINESS_RISK)).fetchone())

    filled = int(t.get("filled_trades", 0) or 0)
    closed = int(t.get("closed_trades", 0) or 0)
    live_fills = int(t.get("live_fills", 0) or 0)
    kill_tests = int(a.get("kill_tests", 0) or 0)
    breaches = int(r.get("breach_events", 0) or 0)

    # Earliest paper activity = first fill or first bot.started/kill event.
    starts: list[datetime] = []
    for key, src in (("first_fill", t), ("first_activity", a)):
        raw = src.get(key)
        if raw:
            dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            starts.append(dt)
    first = min(starts) if starts else None
    days_in_paper = (datetime.now(timezone.utc) - first).days if first else 0

    def crit(name: str, ok: bool, value, threshold, detail: str) -> dict:
        return {"name": name, "pass": bool(ok), "value": value, "threshold": threshold, "detail": detail}

    criteria = [
        crit("paper_duration", days_in_paper >= min_paper_days, days_in_paper, min_paper_days,
             f"{days_in_paper} of {min_paper_days} days of paper activity"),
        crit("kill_switch_tested", kill_tests >= 1, kill_tests, 1,
             "kill switch exercised at least once" if kill_tests else "never tested — run a kill/reset cycle"),
        crit("sufficient_trades", filled >= min_trades, filled, min_trades,
             f"{filled} of {min_trades} filled trades"),
        crit("round_trips_closed", closed >= 1, closed, 1,
             "positions have closed (realized P&L tracked)" if closed else "no round trips closed yet"),
        crit("still_paper", live_fills == 0, live_fills, 0,
             "no live fills" if live_fills == 0 else f"{live_fills} LIVE fills present — investigate"),
    ]
    advisory = [
        crit("risk_breaches", breaches == 0, breaches, 0,
             "no drawdown/limit breach events" if breaches == 0 else f"{breaches} breach event(s) — review before live"),
    ]

    ready = all(c["pass"] for c in criteria)
    return {
        "ready": ready,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "days_in_paper": days_in_paper,
        "thresholds": {"min_paper_days": min_paper_days, "min_trades": min_trades},
        "criteria": criteria,
        "advisory": advisory,
        "summary": {
            "filled_trades": filled,
            "closed_trades": closed,
            "live_fills": live_fills,
            "kill_switch_tests": kill_tests,
            "risk_breach_events": breaches,
        },
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


# Daily funnel from the continuous aggregate (migration 006). Falls back to a
# direct GROUP BY over opportunities when the cagg doesn't exist yet, so the
# endpoint works on databases that haven't run 006.
_FUNNEL_DAILY_CAGG = text("""
    SELECT day, strategy_type, detected, ai_scored, risk_approved,
           executed, risk_rejected, expired
    FROM opportunities_funnel_daily
    WHERE day >= NOW() - make_interval(days => :days)
    ORDER BY day DESC, strategy_type
""")

_FUNNEL_DAILY_DIRECT = text("""
    SELECT
        date_trunc('day', detected_at)                    AS day,
        strategy_type,
        COUNT(*)                                          AS detected,
        COUNT(*) FILTER (WHERE ai_scored_at IS NOT NULL)  AS ai_scored,
        COUNT(*) FILTER (WHERE risk_approved = true)      AS risk_approved,
        COUNT(*) FILTER (WHERE executed = true)           AS executed,
        COUNT(*) FILTER (WHERE risk_approved = false)     AS risk_rejected,
        COUNT(*) FILTER (WHERE expired = true)            AS expired
    FROM opportunities
    WHERE detected_at >= NOW() - make_interval(days => :days)
    GROUP BY 1, 2
    ORDER BY 1 DESC, 2
""")


async def funnel_daily(db_engine: AsyncEngine, *, days: int = 30) -> dict:
    """Per-day, per-strategy funnel rollup (continuous aggregate when available)."""
    source = "continuous_aggregate"
    try:
        async with get_async_session(db_engine) as session:
            rows = (await session.execute(_FUNNEL_DAILY_CAGG, {"days": days})).fetchall()
    except Exception:
        # View missing (migration 006 not applied) — aggregate directly.
        source = "direct"
        async with get_async_session(db_engine) as session:
            rows = (await session.execute(_FUNNEL_DAILY_DIRECT, {"days": days})).fetchall()

    return {"days": days, "source": source, "rows": [_row_to_dict(r) for r in rows]}


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


# ── Positions ─────────────────────────────────────────────────────────────────

_SELECT_POSITIONS = text("""
    SELECT
        venue, symbol, strategy_type, paper_mode,
        net_qty, avg_price, open_fees, realized_pnl, fee_currency,
        status, opened_at, closed_at, updated_at
    FROM positions
    WHERE
        (:status        IS NULL OR status = :status)
        AND (:strategy_type IS NULL OR strategy_type = :strategy_type)
        AND (:paper_mode    IS NULL OR paper_mode = :paper_mode::boolean)
    ORDER BY (status = 'open') DESC, updated_at DESC
    LIMIT :limit OFFSET :offset
""")


async def list_positions(
    db_engine: AsyncEngine,
    *,
    status: str | None = None,
    strategy_type: str | None = None,
    paper_mode: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """
    Return positions (net exposure + cumulative realized P&L) per trading key.

    Defaults to all; pass status='open' for live exposure only. Summing
    realized_pnl across all positions equals the trades-table realized P&L total.
    """
    params = {
        "status": status,
        "strategy_type": strategy_type,
        "paper_mode": str(paper_mode).lower() if paper_mode is not None else None,
        "limit": min(limit, 500),
        "offset": offset,
    }
    async with get_async_session(db_engine) as session:
        rows = await session.execute(_SELECT_POSITIONS, params)
    return [_row_to_dict(r) for r in rows]


# ── Reconciliation helpers ────────────────────────────────────────────────────

_STALE_TRADES = text("""
    SELECT client_order_id, symbol, venue, side, opened_at, strategy_type
    FROM trades
    WHERE
        status IN ('pending', 'open')
        AND opened_at < NOW() - make_interval(mins => :stale_minutes)
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

# Real concurrent exposure: distinct net positions currently open. Uses the
# positions ledger (migration 003) rather than trades — market orders fill/reject
# immediately so trades are ~never left 'pending'/'open', which made the old count
# ~0 and the risk position limit never bind.
_OPEN_POSITION_COUNT = text("""
    SELECT COUNT(*)
    FROM positions
    WHERE status = 'open'
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
    """
    Number of net positions currently open (positions.status='open').

    The journal reconciler writes this to risk_state.open_position_count, which the
    risk engine enforces against RISK_MAX_OPEN_POSITIONS. Now that it reflects real
    exposure, the limit actually binds — tune RISK_MAX_OPEN_POSITIONS accordingly.
    """
    async with get_async_session(db_engine) as session:
        row = (await session.execute(_OPEN_POSITION_COUNT)).fetchone()
    return int(row[0]) if row else 0
