"""
Trade persistence — INSERT fills into the trades table.

Uses raw SQL text() for performance on the execution hot path.
INSERT ... ON CONFLICT DO NOTHING makes crash-recovery replays safe:
if the executor crashes after inserting but before ACKing the stream
message, the replay will silently skip the duplicate row.

Schema defined in: shared/mezna_shared/models/trade.py
TimescaleDB hypertable on opened_at.
"""

import json
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mezna_shared.db import get_async_session
from .adapters import OrderRequest, OrderResult

log = structlog.get_logger()

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
        raw_response,
        opened_at,
        filled_at
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
        CAST(:raw_response AS JSONB),
        :opened_at,
        :filled_at
    )
    ON CONFLICT (client_order_id) DO NOTHING
    RETURNING id
""")


async def persist_trade(
    db_engine: AsyncEngine,
    order: OrderRequest,
    result: OrderResult,
    opportunity_id: str | None,
) -> uuid.UUID | None:
    """
    Persist a trade fill to the trades table.

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
        "raw_response": json.dumps(result.raw_response) if result.raw_response else "{}",
        "opened_at": now,
        "filled_at": filled_at,
    }

    try:
        async with get_async_session(db_engine) as session:
            row = await session.execute(_INSERT_TRADE, params)
            returned = row.fetchone()

        if returned:
            log.info(
                "db.trade_persisted",
                trade_id=str(trade_id),
                symbol=order.symbol,
                side=order.side,
                status=result.status,
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
