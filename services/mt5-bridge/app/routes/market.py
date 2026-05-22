"""
Market data endpoints.

GET /tick/{symbol}       — current bid/ask/spread for one MT5 symbol
GET /symbol/{symbol}     — contract spec (contract size, lot limits, digits)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..mt5_client import get_tick, get_symbol_info, MT5ConnectionError
from ..config import settings
from .auth import require_api_key

router = APIRouter()


class TickResponse(BaseModel):
    symbol: str
    bid: float
    ask: float
    last: float
    spread_points: int
    mid: float
    time: int


class SymbolResponse(BaseModel):
    name: str
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    point: float
    digits: int
    trade_mode: int
    tradeable: bool


@router.get("/tick/{symbol}", response_model=TickResponse)
async def get_tick_endpoint(
    symbol: str,
    _: None = Depends(require_api_key),
) -> TickResponse:
    mt5_symbol = settings.to_mt5_symbol(symbol)
    try:
        tick = await get_tick(mt5_symbol)
    except MT5ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return TickResponse(
        symbol=tick.symbol,
        bid=tick.bid,
        ask=tick.ask,
        last=tick.last,
        spread_points=tick.spread_points,
        mid=round((tick.bid + tick.ask) / 2, 8),
        time=tick.time,
    )


@router.get("/symbol/{symbol}", response_model=SymbolResponse)
async def get_symbol_endpoint(
    symbol: str,
    _: None = Depends(require_api_key),
) -> SymbolResponse:
    mt5_symbol = settings.to_mt5_symbol(symbol)
    try:
        info = await get_symbol_info(mt5_symbol)
    except MT5ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return SymbolResponse(
        name=info.name,
        contract_size=info.contract_size,
        volume_min=info.volume_min,
        volume_max=info.volume_max,
        volume_step=info.volume_step,
        point=info.point,
        digits=info.digits,
        trade_mode=info.trade_mode,
        tradeable=info.trade_mode in (4, 2),   # TRADE_MODE_FULL=4, TRADE_MODE_LONGONLY=2
    )
