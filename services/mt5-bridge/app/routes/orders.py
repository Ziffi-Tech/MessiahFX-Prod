"""
Order placement endpoints.

POST /order/place    — submit a market order (position_usd → lot sizing → MT5)
POST /order/close    — close an open position by ticket number
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..mt5_client import (
    MT5_AVAILABLE,
    calculate_lots,
    close_position,
    get_tick,
    place_order,
    MT5ConnectionError,
    MT5NotAvailable,
    MT5OrderError,
)
from .auth import require_api_key

log = structlog.get_logger()
router = APIRouter()


class PlaceOrderRequest(BaseModel):
    symbol: str = Field(..., description="Internal symbol format (e.g. EUR/USD, XAU/USD, US30)")
    side: str = Field(..., pattern="^(buy|sell)$")
    position_usd: float = Field(..., gt=0, description="Position size in USD to convert to lots")
    client_order_id: str = Field(..., description="Idempotency key from executor")
    strategy_type: str = Field(default="swing")
    paper_mode: bool = Field(default=True)
    comment: str = Field(default="MeznaQuantFX")


class PlaceOrderResponse(BaseModel):
    client_order_id: str
    mt5_order_id: int | None
    mt5_deal_id: int | None
    symbol: str
    mt5_symbol: str
    side: str
    lots: float
    fill_price: float
    position_usd: float
    status: str           # "filled" | "simulated" | "error"
    rejection_reason: str | None
    paper_mode: bool


class CloseOrderRequest(BaseModel):
    ticket: int = Field(..., description="MT5 position ticket number")
    client_order_id: str


class CloseOrderResponse(BaseModel):
    client_order_id: str
    ticket: int
    mt5_order_id: int | None
    fill_price: float
    status: str
    rejection_reason: str | None


@router.post("/order/place", response_model=PlaceOrderResponse)
async def place_order_endpoint(
    body: PlaceOrderRequest,
    _: None = Depends(require_api_key),
) -> PlaceOrderResponse:
    """
    Convert a USD position size to lots and submit a market order to MT5.

    In paper_mode: returns a simulated fill using live bid/ask from MT5.
    In live mode: submits the actual order to MT5 terminal.

    Lot sizing: uses live symbol info from MT5 for accurate lot calculation.
    """
    mt5_symbol = settings.to_mt5_symbol(body.symbol)

    log.info(
        "mt5_bridge.order_received",
        client_order_id=body.client_order_id,
        symbol=body.symbol,
        mt5_symbol=mt5_symbol,
        side=body.side,
        position_usd=body.position_usd,
        paper_mode=body.paper_mode,
    )

    if not MT5_AVAILABLE:
        return PlaceOrderResponse(
            client_order_id=body.client_order_id,
            mt5_order_id=None,
            mt5_deal_id=None,
            symbol=body.symbol,
            mt5_symbol=mt5_symbol,
            side=body.side,
            lots=0.0,
            fill_price=0.0,
            position_usd=body.position_usd,
            status="error",
            rejection_reason="MetaTrader5 package not installed on this host",
            paper_mode=body.paper_mode,
        )

    # ── Get live tick for pricing ──────────────────────────────────────────
    try:
        tick = await get_tick(mt5_symbol)
    except MT5ConnectionError as exc:
        raise HTTPException(status_code=503, detail=f"MT5 tick unavailable: {exc}")

    ref_price = tick.ask if body.side == "buy" else tick.bid

    # ── Calculate lots ────────────────────────────────────────────────────
    try:
        lots = await calculate_lots(
            symbol=mt5_symbol,
            position_usd=body.position_usd,
            price=ref_price,
            min_lot=settings.MIN_LOT_SIZE,
            max_lot=settings.MAX_LOT_SIZE,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Lot calculation failed: {exc}")

    log.info(
        "mt5_bridge.lots_calculated",
        symbol=mt5_symbol,
        position_usd=body.position_usd,
        lots=lots,
        ref_price=ref_price,
    )

    # ── Paper mode: simulate fill ─────────────────────────────────────────
    if body.paper_mode:
        log.info(
            "mt5_bridge.paper_fill",
            symbol=mt5_symbol,
            side=body.side,
            lots=lots,
            price=ref_price,
        )
        return PlaceOrderResponse(
            client_order_id=body.client_order_id,
            mt5_order_id=None,
            mt5_deal_id=None,
            symbol=body.symbol,
            mt5_symbol=mt5_symbol,
            side=body.side,
            lots=lots,
            fill_price=ref_price,
            position_usd=body.position_usd,
            status="simulated",
            rejection_reason=None,
            paper_mode=True,
        )

    # ── Live mode: submit to MT5 ──────────────────────────────────────────
    try:
        fill = await place_order(
            symbol=mt5_symbol,
            side=body.side,
            lots=lots,
            deviation=settings.DEFAULT_DEVIATION,
            magic=settings.MAGIC_NUMBER,
            comment=body.comment[:31],   # MT5 comment max length is 31 chars
        )
        log.info(
            "mt5_bridge.order_filled",
            client_order_id=body.client_order_id,
            mt5_order_id=fill.order_id,
            mt5_deal_id=fill.deal_id,
            symbol=mt5_symbol,
            side=body.side,
            lots=fill.volume,
            fill_price=fill.price,
        )
        return PlaceOrderResponse(
            client_order_id=body.client_order_id,
            mt5_order_id=fill.order_id,
            mt5_deal_id=fill.deal_id,
            symbol=body.symbol,
            mt5_symbol=mt5_symbol,
            side=body.side,
            lots=fill.volume,
            fill_price=fill.price,
            position_usd=body.position_usd,
            status="filled",
            rejection_reason=None,
            paper_mode=False,
        )

    except (MT5NotAvailable, MT5ConnectionError, MT5OrderError) as exc:
        log.error(
            "mt5_bridge.order_rejected",
            client_order_id=body.client_order_id,
            symbol=mt5_symbol,
            error=str(exc),
        )
        return PlaceOrderResponse(
            client_order_id=body.client_order_id,
            mt5_order_id=None,
            mt5_deal_id=None,
            symbol=body.symbol,
            mt5_symbol=mt5_symbol,
            side=body.side,
            lots=lots,
            fill_price=0.0,
            position_usd=body.position_usd,
            status="rejected",
            rejection_reason=str(exc),
            paper_mode=False,
        )


@router.post("/order/close", response_model=CloseOrderResponse)
async def close_order_endpoint(
    body: CloseOrderRequest,
    _: None = Depends(require_api_key),
) -> CloseOrderResponse:
    """Close an open MT5 position by ticket number."""
    if not MT5_AVAILABLE:
        raise HTTPException(status_code=503, detail="MetaTrader5 package not installed")

    try:
        fill = await close_position(
            ticket=body.ticket,
            deviation=settings.DEFAULT_DEVIATION,
            magic=settings.MAGIC_NUMBER,
        )
        return CloseOrderResponse(
            client_order_id=body.client_order_id,
            ticket=body.ticket,
            mt5_order_id=fill.order_id,
            fill_price=fill.price,
            status="filled",
            rejection_reason=None,
        )
    except MT5OrderError as exc:
        return CloseOrderResponse(
            client_order_id=body.client_order_id,
            ticket=body.ticket,
            mt5_order_id=None,
            fill_price=0.0,
            status="rejected",
            rejection_reason=str(exc),
        )
