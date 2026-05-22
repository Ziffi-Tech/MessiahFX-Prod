"""
Historical data fetcher — Binance public REST API.

Downloads 1-minute OHLCV candlestick data and historical funding rates.
No API key required — all endpoints are public.

Candle format returned:
  [timestamp_ms, open, high, low, close, volume, ...]

We normalise to dicts: {ts, open, high, low, close, volume}

Funding rate format:
  [{fundingTime, fundingRate, symbol}, ...]

Paginates automatically up to MAX_TOTAL_CANDLES.
"""

from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from .config import Settings

log = structlog.get_logger()

_KLINES_PATH = "/api/v3/klines"
_FUNDING_PATH = "/fapi/v1/fundingRate"


def _ts_to_dt(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


async def fetch_candles(
    client: httpx.AsyncClient,
    settings: Settings,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    """
    Download OHLCV candles from Binance spot API in paginated batches.

    symbol: Binance spot format, e.g. "BTCUSDT"
    interval: "1m", "5m", "1h", "1d", etc.
    start_ms / end_ms: Unix milliseconds

    Returns list of dicts: {ts, open, high, low, close, volume}
    """
    url = settings.BINANCE_BASE_URL + _KLINES_PATH
    all_candles: list[dict] = []
    current_start = start_ms

    while current_start < end_ms:
        if len(all_candles) >= settings.MAX_TOTAL_CANDLES:
            log.warning(
                "data.max_candles_reached",
                limit=settings.MAX_TOTAL_CANDLES,
                symbol=symbol,
            )
            break

        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": settings.MAX_CANDLES_PER_FETCH,
        }

        try:
            resp = await client.get(url, params=params, timeout=15.0)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:
            log.error("data.klines_fetch_error", symbol=symbol, error=str(exc))
            raise

        if not raw:
            break

        for row in raw:
            all_candles.append({
                "ts": row[0],
                "ts_dt": _ts_to_dt(row[0]),
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": float(row[5]),
                "mid":    (float(row[1]) + float(row[4])) / 2,
            })

        # Advance past last candle timestamp
        current_start = raw[-1][0] + 1
        if len(raw) < settings.MAX_CANDLES_PER_FETCH:
            break

    log.info(
        "data.candles_fetched",
        symbol=symbol,
        interval=interval,
        count=len(all_candles),
    )
    return all_candles


async def fetch_perp_candles(
    client: httpx.AsyncClient,
    settings: Settings,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    """
    Download OHLCV candles from Binance futures (FAPI) API.

    symbol: Binance perp format, e.g. "BTCUSDT" (same as spot on FAPI)
    """
    url = settings.BINANCE_FAPI_URL + "/fapi/v1/klines"
    all_candles: list[dict] = []
    current_start = start_ms

    while current_start < end_ms:
        if len(all_candles) >= settings.MAX_TOTAL_CANDLES:
            break

        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": settings.MAX_CANDLES_PER_FETCH,
        }

        try:
            resp = await client.get(url, params=params, timeout=15.0)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:
            log.error("data.perp_klines_error", symbol=symbol, error=str(exc))
            raise

        if not raw:
            break

        for row in raw:
            all_candles.append({
                "ts": row[0],
                "ts_dt": _ts_to_dt(row[0]),
                "open":   float(row[1]),
                "high":   float(row[2]),
                "low":    float(row[3]),
                "close":  float(row[4]),
                "volume": float(row[5]),
                "mid":    (float(row[1]) + float(row[4])) / 2,
            })

        current_start = raw[-1][0] + 1
        if len(raw) < settings.MAX_CANDLES_PER_FETCH:
            break

    log.info(
        "data.perp_candles_fetched",
        symbol=symbol,
        count=len(all_candles),
    )
    return all_candles


async def fetch_funding_rates(
    client: httpx.AsyncClient,
    settings: Settings,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    """
    Download historical funding rates from Binance FAPI.

    Returns list of {ts, ts_dt, rate} dicts.
    Funding payments occur every 8 hours for most symbols.
    """
    url = settings.BINANCE_FAPI_URL + _FUNDING_PATH
    all_rates: list[dict] = []
    current_start = start_ms

    while current_start < end_ms:
        params = {
            "symbol": symbol,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1000,
        }

        try:
            resp = await client.get(url, params=params, timeout=15.0)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as exc:
            log.error("data.funding_fetch_error", symbol=symbol, error=str(exc))
            # Non-fatal — return empty list
            return []

        if not raw:
            break

        for row in raw:
            all_rates.append({
                "ts": int(row["fundingTime"]),
                "ts_dt": _ts_to_dt(int(row["fundingTime"])),
                "rate": float(row["fundingRate"]),
                "rate_bps": float(row["fundingRate"]) * 10_000,
            })

        current_start = raw[-1]["fundingTime"] + 1
        if len(raw) < 1000:
            break

    log.info(
        "data.funding_rates_fetched",
        symbol=symbol,
        count=len(all_rates),
    )
    return all_rates
