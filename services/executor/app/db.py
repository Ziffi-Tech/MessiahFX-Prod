"""
Trade persistence — INSERT fills into the trades table and maintain positions.

Uses raw SQL text() for performance on the execution hot path.
INSERT ... ON CONFLICT DO NOTHING makes crash-recovery replays safe:
if the executor crashes after inserting but before ACKing the stream
message, the replay will silently skip the duplicate row.

Realized P&L (added in migration 003):
  For every FILLED leg, the fill is applied to the position for its
  (venue, symbol, strategy_type, paper_mode) key using average-cost accounting
  (mezna_shared.pnl.apply_fill). The NET realized P&L produced by that fill is
  written to trades.realized_pnl (0 for opens/adds, ±x when it reduces or closes
  a position), and the positions ledger is upserted with the new net exposure.

  The position update is applied ONLY when the trade INSERT actually inserts a
  new row (RETURNING id). On an idempotent replay the INSERT is a no-op, so the
  position is left untouched — fills are never double-counted.

  All of this happens in ONE transaction (get_async_session commits on exit),
  so a trade row and its position effect are always consistent.

Schema defined in: shared/mezna_shared/models/trade.py + position.py
"""

import json
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mezna_shared.db import get_async_session
from mezna_shared.pnl import apply_fill, PositionState, FLAT
from .adapters import OrderRequest, OrderResult

log = structlog.get_logger()

_POS_EPS = 1e-9

_INSERT_TRADE = text("""
    INSERT INTO trades (
        id,
        opportunity_id,
        venue,
        exchange_order_id,
        client_order_id,
        symbol,
        side,
        order_type,
        quantity,
        filled_qty,
        average_fill_price,
        fee,
        fee_currency,
        slippage_bps,
        status,
        strategy_type,
        paper_mode,
        rejection_reason,
        realized_pnl,
        realized_pnl_currency,
        raw_response,
        opened_at,
        filled_at,
        closed_at
    ) VALUES (
        :id,
        :opportunity_id,
        :venue,
        :exchange_order_id,
        :client_order_id,
        :symbol,
        :side,
        :order_type,
        :quantity,
        :filled_qty,
        :average_fill_price,
        :fee,
        :fee_currency,
        :slippage_bps,
        :status,
        :strategy_type,
        :paper_mode,
        :rejection_reason,
        :realized_pnl,
        :realized_pnl_currency,
        CAST(:raw_response AS JSONB),
        :opened_at,
        :filled_at,
        :closed_at
    )
    ON CONFLICT (client_order_id) DO NOTHING
    RETURNING id
""")

_SELECT_POSITION_FOR_UPDATE = text("""
    SELECT net_qty, avg_price, open_fees, realized_pnl, status, opened_at, closed_at
    FROM positions
    WHERE venue = :venue
      AND symbol = :symbol
      AND strategy_type = :strategy_type
      AND paper_mode = :paper_mode
    FOR UPDATE
""")

_UPSERT_POSITION = text("""
    INSERT INTO positions (
        id, venue, symbol, strategy_type, paper_mode,
        net_qty, avg_price, open_fees, realized_pnl, fee_currency,
        status, opened_at, closed_at, updated_at
    ) VALUES (
        gen_random_uuid(), :venue, :symbol, :strategy_type, :paper_mode,
        :net_qty, :avg_price, :open_fees, :realized_pnl, :fee_currency,
        :status, :opened_at, :closed_at, :now
    )
    ON CONFLICT ON CONSTRAINT uq_positions_key DO UPDATE SET
        net_qty      = EXCLUDED.net_qty,
        avg_price    = EXCLUDED.avg_price,
        open_fees    = EXCLUDED.open_fees,
        realized_pnl = EXCLUDED.realized_pnl,
        fee_currency = COALESCE(EXCLUDED.fee_currency, positions.fee_currency),
        status       = EXCLUDED.status,
        opened_at    = EXCLUDED.opened_at,
        closed_at    = EXCLUDED.closed_at,
        updated_at   = EXCLUDED.updated_at
""")


def _is_fill(result: OrderResult) -> bool:
    """True when the result is a real fill that should move a position."""
    try:
        return (
            result.status == "filled"
            and float(result.filled_qty) > 0.0
            and result.average_fill_price is not None
            and float(result.average_fill_price) > 0.0
        )
    except (TypeError, ValueError):
        return False


async def persist_trade(
    db_engine: AsyncEngine,
    order: OrderRequest,
    result: OrderResult,
    opportunity_id: str | None,
) -> uuid.UUID | None:
    """
    Persist a trade fill to the trades table and update its position.

    Returns the trade UUID on success, or None if the row already existed
    (idempotent replay — the executor crashed and reprocessed the message).

    Raises on unexpected database errors — caller should log and continue
    so a DB blip doesn't halt the entire execution pipeline.
    """
    now = datetime.now(timezone.utc)
    trade_id = uuid.uuid4()
    filled_at = now if result.status == "filled" else None

    # Coerce opportunity_id to UUID — malformed IDs become None
    try:
        opp_uuid = uuid.UUID(opportunity_id) if opportunity_id else None
    except (ValueError, TypeError):
        opp_uuid = None
        log.warning(
            "db.malformed_opportunity_id",
            raw=opportunity_id,
            client_order_id=result.client_order_id,
        )

    strat = order.strategy_type or "unknown"
    is_fill = _is_fill(result)

    try:
        async with get_async_session(db_engine) as session:
            # ── Compute position effect for real fills (under row lock) ────────
            realized_pnl: float | None = None
            realized_ccy: str | None = None
            closed_at: datetime | None = None
            pos_params: dict | None = None

            if is_fill:
                pos_row = (
                    await session.execute(_SELECT_POSITION_FOR_UPDATE, {
                        "venue": order.venue,
                        "symbol": order.symbol,
                        "strategy_type": strat,
                        "paper_mode": order.paper_mode,
                    })
                ).fetchone()

                if pos_row is not None:
                    current = PositionState(
                        float(pos_row.net_qty), float(pos_row.avg_price), float(pos_row.open_fees)
                    )
                    prior_realized = float(pos_row.realized_pnl or 0.0)
                    prior_flat = pos_row.status != "open" or abs(float(pos_row.net_qty)) <= _POS_EPS
                    prior_opened_at = pos_row.opened_at
                    prior_closed_at = pos_row.closed_at
                else:
                    current = FLAT
                    prior_realized = 0.0
                    prior_flat = True
                    prior_opened_at = None
                    prior_closed_at = None

                outcome = apply_fill(
                    current, order.side,
                    float(result.filled_qty), float(result.average_fill_price),
                    float(result.fee or 0.0),
                )
                realized_pnl = round(outcome.realized_pnl, 8)
                realized_ccy = result.fee_currency
                now_open = abs(outcome.position.net_qty) > _POS_EPS

                if outcome.closed and not now_open:
                    closed_at = now  # this fill flattened the position

                # opened_at / closed_at transitions for the position ledger
                if now_open:
                    opened_at = now if prior_flat else prior_opened_at
                    pos_closed_at = prior_closed_at
                else:
                    opened_at = prior_opened_at
                    pos_closed_at = now if not prior_flat else prior_closed_at

                pos_params = {
                    "venue": order.venue,
                    "symbol": order.symbol,
                    "strategy_type": strat,
                    "paper_mode": order.paper_mode,
                    "net_qty": round(outcome.position.net_qty, 8),
                    "avg_price": round(outcome.position.avg_price, 8),
                    "open_fees": round(outcome.position.open_fees, 8),
                    "realized_pnl": round(prior_realized + outcome.realized_pnl, 8),
                    "fee_currency": result.fee_currency,
                    "status": "open" if now_open else "flat",
                    "opened_at": opened_at,
                    "closed_at": pos_closed_at,
                    "now": now,
                }

            # ── Insert the trade (idempotent) ─────────────────────────────────
            params = {
                "id": str(trade_id),
                "opportunity_id": str(opp_uuid) if opp_uuid else None,
                "venue": order.venue,
                "exchange_order_id": result.exchange_order_id,
                "client_order_id": result.client_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "order_type": order.order_type,
                "quantity": float(order.quantity),
                "filled_qty": float(result.filled_qty),
                "average_fill_price": float(result.average_fill_price) if result.average_fill_price else None,
                "fee": float(result.fee),
                "fee_currency": result.fee_currency,
                "slippage_bps": float(result.slippage_bps),
                "status": result.status,
                "strategy_type": order.strategy_type,
                "paper_mode": order.paper_mode,
                "rejection_reason": result.rejection_reason,
                "realized_pnl": realized_pnl,
                "realized_pnl_currency": realized_ccy,
                "raw_response": json.dumps(result.raw_response) if result.raw_response else "{}",
                "opened_at": now,
                "filled_at": filled_at,
                "closed_at": closed_at,
            }
            returned = (await session.execute(_INSERT_TRADE, params)).fetchone()

            # ── Apply the position change ONLY for newly-inserted fills ────────
            if returned and is_fill and pos_params is not None:
                await session.execute(_UPSERT_POSITION, pos_params)

        if returned:
            log.info(
                "db.trade_persisted",
                trade_id=str(trade_id),
                symbol=order.symbol,
                side=order.side,
                status=result.status,
                realized_pnl=realized_pnl,
                paper=order.paper_mode,
            )
            return trade_id
        else:
            log.warning(
                "db.trade_duplicate_skipped",
                client_order_id=result.client_order_id,
                hint="executor crashed after insert but before ACK — safe to ignore",
            )
            return None

    except Exception as exc:
        log.error(
            "db.trade_persist_failed",
            client_order_id=result.client_order_id,
            symbol=order.symbol,
            error=str(exc),
        )
        raise
