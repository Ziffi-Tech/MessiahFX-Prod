"""
Funding Rate Arbitrage Strategy.

Concept:
  Binance USDM perpetual contracts settle funding every 8 hours (00:00, 08:00,
  16:00 UTC). The rate can be positive or negative, depending on market sentiment.

  When the rate is significantly positive:
    - Shorts (perp) RECEIVE funding from longs.
    - A delta-neutral position (long spot + short perp) captures this yield
      with no directional market exposure.

  Net edge per 8h period:
    funding_bps - entry_spread_cost_bps - round_trip_fee_bps

Signal conditions:
  1. funding_rate > 0   (longs pay; being short is paid)
  2. net_edge_bps > FUNDING_ARB_MIN_EDGE_BPS

Data flow:
  - Tick data (bid/ask/spread_bps) read from Redis (written by market-data service)
  - Funding rate fetched from Binance FAPI public REST endpoint (no auth needed)
  - Funding rate is cached for FUNDING_ARB_POLL_SECONDS to avoid API hammering

Funding rate typical values:
  Normal market:  0.01% per 8h  =  1 bps   (usually negative edge after fees)
  Bull run:       0.10% per 8h  =  10 bps  (profitable with tight spreads)
  Extreme:        0.30% per 8h  =  30 bps  (very attractive)
"""

import time
from datetime import datetime, timezone

import httpx
import structlog
from redis.asyncio import Redis

from ..config import Settings
from ..publisher import publish_opportunity
from .base import read_latest_tick
from mezna_shared.schemas.opportunity import OpportunityCreate

log = structlog.get_logger()

STRATEGY_NAME = "funding_arb"
_FUNDING_PATH = "/fapi/v1/premiumIndex"


def _spot_to_perp(spot: str) -> str:
    """'BTC/USDT'  →  'BTC/USDT:USDT'  (CCXT linear perp format)"""
    base, quote = spot.split("/")
    return f"{base}/{quote}:{quote}"


def _spot_to_api_symbol(spot: str) -> str:
    """'BTC/USDT'  →  'BTCUSDT'  (Binance REST API format)"""
    return spot.replace("/", "")


class FundingArbStrategy:
    """
    Funding rate arbitrage.

    One instance per service lifetime. The httpx AsyncClient is passed in from
    the runner so it can be shared and properly closed on shutdown.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Cache: api_symbol → (rate_float, monotonic_time_fetched)
        self._rate_cache: dict[str, tuple[float, float]] = {}

    async def _get_funding_rate(self, client: httpx.AsyncClient, api_symbol: str) -> float | None:
        """
        Return the current funding rate as a fraction (e.g., 0.0001 = 0.01%).
        Uses an in-memory cache to avoid hammering the Binance API.
        Returns None on any network or parse error.
        """
        now = time.monotonic()
        cached = self._rate_cache.get(api_symbol)
        if cached is not None:
            rate, fetched_at = cached
            if now - fetched_at < self._settings.FUNDING_ARB_POLL_SECONDS:
                return rate

        url = self._settings.binance_futures_base_url + _FUNDING_PATH
        try:
            resp = await client.get(url, params={"symbol": api_symbol}, timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            rate = float(data["lastFundingRate"])
            self._rate_cache[api_symbol] = (rate, now)
            log.debug("funding_arb.rate_fetched", symbol=api_symbol, rate=rate)
            return rate
        except Exception as exc:
            log.warning("funding_arb.rate_fetch_failed", symbol=api_symbol, error=str(exc))
            return None

    async def run_once(
        self,
        redis: Redis,
        state: dict,
        client: httpx.AsyncClient,
    ) -> None:
        """
        One iteration: check all configured symbols, emit a signal for each
        that has sufficient edge. Called repeatedly by the runner loop.
        """
        paper_mode = self._settings.TRADING_MODE != "live"
        latency = state.get("latency_profile", "standard")
        now = datetime.now(timezone.utc)

        for spot_symbol in self._settings.funding_arb_symbol_list:
            perp_symbol = _spot_to_perp(spot_symbol)
            api_symbol = _spot_to_api_symbol(spot_symbol)

            # ── Read market data ───────────────────────────────────────────────
            spot_tick = await read_latest_tick(redis, "binance", spot_symbol)
            perp_tick = await read_latest_tick(redis, "binance", perp_symbol)

            if not spot_tick or not perp_tick:
                log.debug(
                    "funding_arb.no_tick",
                    spot=spot_symbol,
                    perp=perp_symbol,
                    hint="market-data service may still be connecting",
                )
                continue

            # ── Funding rate ───────────────────────────────────────────────────
            funding_rate = await self._get_funding_rate(client, api_symbol)
            if funding_rate is None:
                continue

            # Only positive funding is interesting (shorts get paid)
            if funding_rate <= 0:
                continue

            funding_bps = funding_rate * 10_000  # fraction → bps

            # ── Edge calculation ───────────────────────────────────────────────
            spot_spread_bps = float(spot_tick.get("spread_bps", 0))
            perp_spread_bps = float(perp_tick.get("spread_bps", 0))
            # Entry cost: half the spread each side (we cross the spread to enter)
            spread_cost_bps = (spot_spread_bps + perp_spread_bps) / 2.0
            fee_bps = self._settings.FUNDING_ARB_FEE_BPS
            net_edge_bps = round(funding_bps - spread_cost_bps - fee_bps, 4)

            log.debug(
                "funding_arb.edge",
                symbol=spot_symbol,
                funding_bps=round(funding_bps, 4),
                spread_cost_bps=round(spread_cost_bps, 4),
                fee_bps=fee_bps,
                net_edge_bps=net_edge_bps,
            )

            if net_edge_bps < self._settings.FUNDING_ARB_MIN_EDGE_BPS:
                continue

            # ── Publish signal ─────────────────────────────────────────────────
            opp = OpportunityCreate(
                strategy_type=STRATEGY_NAME,
                venue="binance",
                source="internal",
                symbol_primary=spot_symbol,     # leg 1: buy spot
                symbol_secondary=perp_symbol,   # leg 2: sell perp
                detected_at=now,
                latency_profile=latency,
                spread=spot_spread_bps,
                funding_rate=funding_rate,
                expected_return_bps=round(funding_bps, 4),
                fee_cost_bps=round(spread_cost_bps + fee_bps, 4),
                net_edge_bps=net_edge_bps,
                paper_mode=paper_mode,
                raw_signal={
                    "spot_bid": spot_tick.get("bid"),
                    "spot_ask": spot_tick.get("ask"),
                    "spot_spread_bps": spot_spread_bps,
                    "perp_bid": perp_tick.get("bid"),
                    "perp_ask": perp_tick.get("ask"),
                    "perp_spread_bps": perp_spread_bps,
                    "funding_rate_raw": funding_rate,
                    "funding_bps": funding_bps,
                },
            )
            await publish_opportunity(redis, opp)

    async def run_from_signal(
        self,
        redis: Redis,
        symbol: str,           # Internal format: "BTC/USDT"
        action: str,           # "buy" | "sell" | "alert"
        state: dict,
        client: httpx.AsyncClient,
        tv_price: float | None = None,
    ) -> bool:
        """
        Process a TradingView signal for this symbol.

        Unlike run_once (which scans all configured symbols), this targets
        exactly one symbol as directed by TradingView.

        Returns True if an opportunity was published, False otherwise.
        """
        perp_symbol = _spot_to_perp(symbol)
        api_symbol = _spot_to_api_symbol(symbol)
        paper_mode = self._settings.TRADING_MODE != "live"
        latency = state.get("latency_profile", "standard")
        now = datetime.now(timezone.utc)

        # ── Market data ────────────────────────────────────────────────────────
        spot_tick = await read_latest_tick(redis, "binance", symbol)
        perp_tick = await read_latest_tick(redis, "binance", perp_symbol)

        if not spot_tick or not perp_tick:
            log.warning(
                "funding_arb.signal.no_tick",
                symbol=symbol,
                hint="market-data service may not be streaming this symbol yet",
            )
            return False

        # ── Funding rate validation ─────────────────────────────────────────
        funding_rate = await self._get_funding_rate(client, api_symbol)
        if funding_rate is None:
            log.warning("funding_arb.signal.no_rate", symbol=symbol)
            return False

        if funding_rate <= 0:
            log.info(
                "funding_arb.signal.skipped",
                symbol=symbol,
                funding_rate=funding_rate,
                reason="funding rate is not positive — no arb edge",
            )
            return False

        funding_bps = funding_rate * 10_000
        spot_spread_bps = float(spot_tick.get("spread_bps", 0))
        perp_spread_bps = float(perp_tick.get("spread_bps", 0))
        spread_cost_bps = (spot_spread_bps + perp_spread_bps) / 2.0
        fee_bps = self._settings.FUNDING_ARB_FEE_BPS
        net_edge_bps = round(funding_bps - spread_cost_bps - fee_bps, 4)

        if net_edge_bps < self._settings.FUNDING_ARB_MIN_EDGE_BPS:
            log.info(
                "funding_arb.signal.edge_insufficient",
                symbol=symbol,
                funding_bps=round(funding_bps, 4),
                net_edge_bps=net_edge_bps,
                min_required=self._settings.FUNDING_ARB_MIN_EDGE_BPS,
            )
            return False

        # ── Publish opportunity ────────────────────────────────────────────
        opp = OpportunityCreate(
            strategy_type=STRATEGY_NAME,
            venue="binance",
            source="tradingview",
            symbol_primary=symbol,
            symbol_secondary=perp_symbol,
            detected_at=now,
            latency_profile=latency,
            spread=spot_spread_bps,
            funding_rate=funding_rate,
            expected_return_bps=round(funding_bps, 4),
            fee_cost_bps=round(spread_cost_bps + fee_bps, 4),
            net_edge_bps=net_edge_bps,
            paper_mode=paper_mode,
            raw_signal={
                "trigger": "tradingview",
                "tv_action": action,
                "tv_price": tv_price,
                "spot_bid": spot_tick.get("bid"),
                "spot_ask": spot_tick.get("ask"),
                "perp_bid": perp_tick.get("bid"),
                "perp_ask": perp_tick.get("ask"),
                "funding_rate_raw": funding_rate,
                "funding_bps": funding_bps,
            },
        )
        await publish_opportunity(redis, opp)
        log.info(
            "funding_arb.signal.published",
            symbol=symbol,
            net_edge_bps=net_edge_bps,
            source="tradingview",
        )
        return True
