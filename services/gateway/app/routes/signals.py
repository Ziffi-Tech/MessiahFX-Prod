"""
Signal ingestion endpoints.

TradingView alert → POST /api/v1/signals/tradingview → validated → Redis queue

Design principles:
- Validate schema strictly (reject malformed alerts early)
- Log every inbound signal for audit
- Never execute directly — publish to queue for strategy layer
- Return fast (webhook must respond within TV's 3s timeout)
"""

import json
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Request, HTTPException, status
from pydantic import ValidationError

from mezna_shared.redis_client import RedisKeys, StreamNames
from mezna_shared.schemas.opportunity import TradingViewSignal

log = structlog.get_logger()
router = APIRouter()

# Max age of a TradingView signal before it's considered stale (seconds)
TV_SIGNAL_MAX_AGE_SECONDS = 60


@router.post(
    "/tradingview",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Receive TradingView Pine Script webhook alert",
)
async def receive_tradingview_signal(
    request: Request,
    payload: dict,
) -> dict:
    """
    Receive and validate a TradingView webhook alert.

    The Pine Script alert message must be a JSON object matching TradingViewSignal schema.
    Non-conforming payloads are rejected with HTTP 422.
    Valid signals are published to the Redis signal queue for strategy evaluation.

    TradingView never receives order confirmation — it only fires signals.
    """
    received_at = datetime.now(timezone.utc)

    log.info(
        "signal.tradingview.received",
        payload_keys=list(payload.keys()),
        received_at=received_at.isoformat(),
    )

    # Validate schema
    try:
        signal = TradingViewSignal(**payload, raw=payload)
    except (ValidationError, Exception) as exc:
        log.warning(
            "signal.tradingview.rejected",
            reason="schema_validation_failed",
            error=str(exc),
            payload=payload,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Signal schema validation failed: {str(exc)}",
        )

    # Route to the dedicated TradingView stream.
    # The strategy service signal_consumer reads from here, validates with
    # live market data (funding rates / z-scores), and emits a proper
    # OpportunityCreate to signals:opportunities if conditions hold.
    # This keeps TV signals separate from internally-generated opportunities.
    redis = request.app.state.redis
    stream_entry = {
        StreamNames.PAYLOAD: json.dumps({
            "strategy": signal.strategy,
            "venue": signal.venue,
            "symbol": signal.symbol,
            "action": signal.action,
            "price": signal.price,
            "note": signal.note,
            "source": "tradingview",
            "received_at": received_at.isoformat(),
        }),
        "source": "tradingview",
    }

    try:
        msg_id = await redis.xadd(
            RedisKeys.SIGNALS_TV,
            stream_entry,
            maxlen=500,       # TV signals are time-sensitive; prune aggressively
            approximate=True,
        )
        log.info(
            "signal.tradingview.queued",
            msg_id=msg_id,
            strategy=signal.strategy,
            venue=signal.venue,
            symbol=signal.symbol,
            action=signal.action,
        )
    except Exception as exc:
        log.error(
            "signal.tradingview.queue_failed",
            error=str(exc),
            signal=signal.model_dump(),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Signal queue unavailable",
        )

    return {
        "accepted": True,
        "msg_id": msg_id,
        "stream": "signals:tradingview",
        "received_at": received_at.isoformat(),
    }
