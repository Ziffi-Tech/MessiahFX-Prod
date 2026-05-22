"""Account and position endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..mt5_client import get_account_info, get_positions, MT5ConnectionError
from ..config import settings
from .auth import require_api_key

router = APIRouter()


class AccountResponse(BaseModel):
    login: int
    server: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    currency: str
    leverage: int
    profit: float


class PositionResponse(BaseModel):
    ticket: int
    symbol: str
    side: str
    volume: float
    open_price: float
    current_price: float
    profit: float
    swap: float
    magic: int
    comment: str
    open_time: int


@router.get("/account", response_model=AccountResponse)
async def account_info(_: None = Depends(require_api_key)) -> AccountResponse:
    try:
        info = await get_account_info()
    except MT5ConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return AccountResponse(
        login=info.login,
        server=info.server,
        balance=info.balance,
        equity=info.equity,
        margin=info.margin,
        free_margin=info.free_margin,
        margin_level=info.margin_level,
        currency=info.currency,
        leverage=info.leverage,
        profit=info.profit,
    )


@router.get("/positions", response_model=list[PositionResponse])
async def open_positions(
    own_only: bool = True,
    _: None = Depends(require_api_key),
) -> list[PositionResponse]:
    """Return open positions. own_only=True filters to MeznaQuantFX magic number only."""
    magic_filter = settings.MAGIC_NUMBER if own_only else None
    positions = await get_positions(magic=magic_filter)
    return [
        PositionResponse(
            ticket=p.ticket,
            symbol=p.symbol,
            side=p.side,
            volume=p.volume,
            open_price=p.open_price,
            current_price=p.current_price,
            profit=p.profit,
            swap=p.swap,
            magic=p.magic,
            comment=p.comment,
            open_time=p.open_time,
        )
        for p in positions
    ]
