"""
MetaTrader 5 client wrapper.

Wraps the MetaTrader5 Python package (Windows-only, COM-based) in a
thread-safe async interface for use in FastAPI route handlers.

Thread safety:
  MT5 Python API is NOT thread-safe — concurrent calls corrupt state.
  All MT5 operations run serialised through a single asyncio.Lock
  via asyncio.to_thread (offloads sync calls to a thread pool without
  blocking the event loop).

Availability:
  If MetaTrader5 is not installed (e.g., during dev on Linux), all
  operations raise MT5NotAvailable. The health endpoint surfaces this.

Connection lifecycle:
  Call connect() at service startup. MT5 may disconnect if the terminal
  closes — reconnect() handles this transparently per request.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger()

# ── Availability guard ─────────────────────────────────────────────────────────
try:
    import MetaTrader5 as mt5  # type: ignore[import-untyped]
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None  # type: ignore[assignment]
    MT5_AVAILABLE = False

# Single global lock — all MT5 calls acquire this
_mt5_lock = asyncio.Lock()


class MT5NotAvailable(RuntimeError):
    """Raised when MetaTrader5 package is not installed."""


class MT5ConnectionError(RuntimeError):
    """Raised when MT5 terminal is not connected or login failed."""


class MT5OrderError(RuntimeError):
    """Raised when an order is rejected by MT5."""


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class TickData:
    symbol: str
    bid: float
    ask: float
    last: float
    spread_points: int
    time: int   # Unix timestamp


@dataclass
class AccountInfo:
    login: int
    server: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float   # equity/margin × 100, or 0 if no positions
    currency: str
    leverage: int
    profit: float


@dataclass
class SymbolInfo:
    name: str
    contract_size: float   # e.g. 100000 for EURUSD, 100 for XAUUSD
    volume_min: float      # minimum lot size
    volume_max: float      # maximum lot size
    volume_step: float     # lot size increment
    point: float           # minimum price change
    digits: int            # decimal places
    trade_mode: int        # 0=disabled, 2=full, etc.


@dataclass
class FillResult:
    order_id: int
    deal_id: int
    volume: float          # lots executed
    price: float           # execution price
    symbol: str
    comment: str
    retcode: int
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PositionInfo:
    ticket: int
    symbol: str
    side: str              # "buy" | "sell"
    volume: float
    open_price: float
    current_price: float
    profit: float
    swap: float
    magic: int
    comment: str
    open_time: int


# ── Sync helper functions (called inside asyncio.to_thread) ───────────────────

def _sync_connect(account: int, password: str, server: str, path: str) -> bool:
    if not MT5_AVAILABLE:
        return False

    kwargs: dict[str, Any] = {}
    if path:
        kwargs["path"] = path

    if not mt5.initialize(**kwargs):
        log.error("mt5.initialize_failed", error=str(mt5.last_error()))
        return False

    if account:
        if not mt5.login(account, password=password, server=server):
            log.error("mt5.login_failed", error=str(mt5.last_error()))
            mt5.shutdown()
            return False

    info = mt5.account_info()
    if info is None:
        log.error("mt5.account_info_failed", error=str(mt5.last_error()))
        return False

    log.info(
        "mt5.connected",
        login=info.login,
        server=info.server,
        balance=info.balance,
        currency=info.currency,
    )
    return True


def _sync_account_info() -> AccountInfo | None:
    if not MT5_AVAILABLE:
        return None
    info = mt5.account_info()
    if info is None:
        return None
    margin_level = (info.equity / info.margin * 100) if info.margin else 0.0
    return AccountInfo(
        login=info.login,
        server=info.server,
        balance=info.balance,
        equity=info.equity,
        margin=info.margin,
        free_margin=info.margin_free,
        margin_level=margin_level,
        currency=info.currency,
        leverage=info.leverage,
        profit=info.profit,
    )


def _sync_tick(symbol: str) -> TickData | None:
    if not MT5_AVAILABLE:
        return None
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return None
    return TickData(
        symbol=symbol,
        bid=tick.bid,
        ask=tick.ask,
        last=tick.last,
        spread_points=int((tick.ask - tick.bid) / mt5.symbol_info(symbol).point)
        if mt5.symbol_info(symbol) else 0,
        time=tick.time,
    )


def _sync_symbol_info(symbol: str) -> SymbolInfo | None:
    if not MT5_AVAILABLE:
        return None
    info = mt5.symbol_info(symbol)
    if info is None:
        # Try to select the symbol first
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    if info is None:
        return None
    return SymbolInfo(
        name=info.name,
        contract_size=info.trade_contract_size,
        volume_min=info.volume_min,
        volume_max=info.volume_max,
        volume_step=info.volume_step,
        point=info.point,
        digits=info.digits,
        trade_mode=info.trade_mode,
    )


def _sync_calculate_lots(
    symbol: str,
    position_usd: float,
    price: float,
    min_lot: float,
    max_lot: float,
) -> float:
    """
    Convert a USD position size to MT5 lots.

    Formula: lots = position_usd / (price × contract_size)

    Then clamped to [min_lot, max_lot] and rounded to volume_step.
    """
    if not MT5_AVAILABLE:
        return min_lot

    info = _sync_symbol_info(symbol)
    if info is None:
        log.warning("mt5.symbol_info_missing", symbol=symbol, fallback=min_lot)
        return min_lot

    if price <= 0 or info.contract_size <= 0:
        return min_lot

    raw_lots = position_usd / (price * info.contract_size)

    # Round to volume_step
    step = info.volume_step or 0.01
    lots = round(round(raw_lots / step) * step, 8)

    # Clamp to [min_lot, max_lot] with global safety cap
    lots = max(max(info.volume_min, min_lot), min(min(info.volume_max, max_lot), lots))

    log.debug(
        "mt5.lots_calculated",
        symbol=symbol,
        position_usd=position_usd,
        price=price,
        contract_size=info.contract_size,
        raw_lots=raw_lots,
        final_lots=lots,
    )
    return lots


def _sync_place_order(
    symbol: str,
    side: str,             # "buy" | "sell"
    lots: float,
    deviation: int,
    magic: int,
    comment: str = "MeznaQuantFX",
) -> FillResult:
    """
    Submit a market order to MT5.

    Tries ORDER_FILLING_IOC first; falls back to ORDER_FILLING_RETURN
    if the broker doesn't support IOC.

    Raises MT5OrderError on rejection.
    """
    if not MT5_AVAILABLE:
        raise MT5NotAvailable("MetaTrader5 package not installed")

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise MT5ConnectionError(f"Cannot get tick for {symbol}")

    order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
    price = tick.ask if side == "buy" else tick.bid

    def _build_request(filling: int) -> dict:
        return {
            "action":      mt5.TRADE_ACTION_DEAL,
            "symbol":      symbol,
            "volume":      lots,
            "type":        order_type,
            "price":       price,
            "deviation":   deviation,
            "magic":       magic,
            "comment":     comment,
            "type_time":   mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

    # Try IOC (immediate-or-cancel), then RETURN (most brokers support one)
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN, mt5.ORDER_FILLING_FOK):
        result = mt5.order_send(_build_request(filling))
        if result is None:
            continue
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(
                "mt5.order_filled",
                symbol=symbol,
                side=side,
                lots=lots,
                price=result.price,
                order_id=result.order,
                deal_id=result.deal,
            )
            return FillResult(
                order_id=result.order,
                deal_id=result.deal,
                volume=result.volume,
                price=result.price,
                symbol=symbol,
                comment=result.comment,
                retcode=result.retcode,
                raw=result._asdict() if hasattr(result, "_asdict") else {},
            )
        if result.retcode not in (
            mt5.TRADE_RETCODE_INVALID_FILL,    # filling mode not supported
        ):
            break  # Different error — don't retry with another filling mode

    # Final result is an error
    retcode = result.retcode if result else -1
    comment_out = result.comment if result else "no result"
    raise MT5OrderError(
        f"Order rejected: retcode={retcode} comment='{comment_out}' "
        f"symbol={symbol} side={side} lots={lots}"
    )


def _sync_close_position(ticket: int, deviation: int, magic: int) -> FillResult:
    """Close a specific open position by ticket number."""
    if not MT5_AVAILABLE:
        raise MT5NotAvailable("MetaTrader5 package not installed")

    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        raise MT5OrderError(f"Position ticket {ticket} not found")

    pos = positions[0]
    close_side = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    price = tick.bid if close_side == mt5.ORDER_TYPE_SELL else tick.ask

    request = {
        "action":      mt5.TRADE_ACTION_DEAL,
        "symbol":      pos.symbol,
        "volume":      pos.volume,
        "type":        close_side,
        "position":    ticket,
        "price":       price,
        "deviation":   deviation,
        "magic":       magic,
        "comment":     "MeznaQuantFX close",
        "type_time":   mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        retcode = result.retcode if result else -1
        raise MT5OrderError(f"Close rejected: retcode={retcode} ticket={ticket}")

    return FillResult(
        order_id=result.order,
        deal_id=result.deal,
        volume=result.volume,
        price=result.price,
        symbol=pos.symbol,
        comment=result.comment,
        retcode=result.retcode,
        raw=result._asdict() if hasattr(result, "_asdict") else {},
    )


def _sync_get_positions(magic: int | None = None) -> list[PositionInfo]:
    if not MT5_AVAILABLE:
        return []
    positions = mt5.positions_get()
    if positions is None:
        return []
    result = []
    for p in positions:
        if magic is not None and p.magic != magic:
            continue
        result.append(PositionInfo(
            ticket=p.ticket,
            symbol=p.symbol,
            side="buy" if p.type == mt5.ORDER_TYPE_BUY else "sell",
            volume=p.volume,
            open_price=p.price_open,
            current_price=p.price_current,
            profit=p.profit,
            swap=p.swap,
            magic=p.magic,
            comment=p.comment,
            open_time=p.time,
        ))
    return result


def _sync_is_connected() -> bool:
    if not MT5_AVAILABLE:
        return False
    try:
        info = mt5.account_info()
        return info is not None
    except Exception:
        return False


# ── Async public API ───────────────────────────────────────────────────────────

async def connect(account: int, password: str, server: str, path: str = "") -> bool:
    """Connect to MT5 terminal. Thread-safe."""
    async with _mt5_lock:
        return await asyncio.to_thread(_sync_connect, account, password, server, path)


async def is_connected() -> bool:
    async with _mt5_lock:
        return await asyncio.to_thread(_sync_is_connected)


async def get_account_info() -> AccountInfo:
    async with _mt5_lock:
        info = await asyncio.to_thread(_sync_account_info)
    if info is None:
        raise MT5ConnectionError("Cannot get account info — terminal may be disconnected")
    return info


async def get_tick(symbol: str) -> TickData:
    async with _mt5_lock:
        tick = await asyncio.to_thread(_sync_tick, symbol)
    if tick is None:
        raise MT5ConnectionError(f"Cannot get tick for {symbol}")
    return tick


async def get_symbol_info(symbol: str) -> SymbolInfo:
    async with _mt5_lock:
        info = await asyncio.to_thread(_sync_symbol_info, symbol)
    if info is None:
        raise MT5ConnectionError(f"Symbol {symbol!r} not found in MT5")
    return info


async def calculate_lots(
    symbol: str,
    position_usd: float,
    price: float,
    min_lot: float = 0.01,
    max_lot: float = 10.0,
) -> float:
    async with _mt5_lock:
        return await asyncio.to_thread(
            _sync_calculate_lots, symbol, position_usd, price, min_lot, max_lot
        )


async def place_order(
    symbol: str,
    side: str,
    lots: float,
    deviation: int = 20,
    magic: int = 234000,
    comment: str = "MeznaQuantFX",
) -> FillResult:
    async with _mt5_lock:
        return await asyncio.to_thread(
            _sync_place_order, symbol, side, lots, deviation, magic, comment
        )


async def close_position(ticket: int, deviation: int = 20, magic: int = 234000) -> FillResult:
    async with _mt5_lock:
        return await asyncio.to_thread(_sync_close_position, ticket, deviation, magic)


async def get_positions(magic: int | None = None) -> list[PositionInfo]:
    async with _mt5_lock:
        return await asyncio.to_thread(_sync_get_positions, magic)


async def shutdown() -> None:
    if MT5_AVAILABLE:
        async with _mt5_lock:
            await asyncio.to_thread(mt5.shutdown)
        log.info("mt5.shutdown")
