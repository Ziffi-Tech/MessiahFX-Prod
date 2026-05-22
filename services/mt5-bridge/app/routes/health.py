"""Health endpoints for the MT5 bridge service."""

from fastapi import APIRouter
from pydantic import BaseModel

from ..mt5_client import MT5_AVAILABLE, is_connected, get_account_info
from ..config import settings

router = APIRouter()


class LiveResponse(BaseModel):
    status: str
    service: str
    version: str
    mt5_package: bool


class ReadyResponse(BaseModel):
    status: str
    mt5_package: bool
    mt5_connected: bool
    account: int | None
    server: str | None
    balance: float | None
    currency: str | None
    api_key_set: bool


@router.get("/health/live", response_model=LiveResponse)
async def liveness() -> LiveResponse:
    return LiveResponse(
        status="ok",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
        mt5_package=MT5_AVAILABLE,
    )


@router.get("/health/ready", response_model=ReadyResponse)
async def readiness() -> ReadyResponse:
    connected = await is_connected()
    account = None
    server_name = None
    balance = None
    currency = None

    if connected:
        try:
            info = await get_account_info()
            account = info.login
            server_name = info.server
            balance = info.balance
            currency = info.currency
        except Exception:
            connected = False

    return ReadyResponse(
        status="ok" if (MT5_AVAILABLE and connected) else "degraded",
        mt5_package=MT5_AVAILABLE,
        mt5_connected=connected,
        account=account,
        server=server_name,
        balance=balance,
        currency=currency,
        api_key_set=bool(settings.BRIDGE_API_KEY),
    )
