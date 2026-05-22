"""
Strategy runner — orchestrates all strategy tasks.

Signal modes (controlled by settings.TV_SIGNAL_MODE):

  TV_SIGNAL_MODE = True  (default — recommended)
  ─────────────────────────────────────────────
  Bot ONLY trades when TradingView fires a webhook.
  Autonomous market-data loops are inactive (sleep at _TV_MODE_IDLE_SLEEP).
  The signal_consumer task reads signals:tradingview and dispatches to
  the appropriate strategy's run_from_signal() handler.

  TV_SIGNAL_MODE = False  (autonomous mode)
  ─────────────────────────────────────────
  Original behaviour: strategies scan market data continuously.
  The signal_consumer still runs — TV signals work in addition.
  Useful for running both autonomous + TV-triggered opportunities.

Kill switch precedence (always applies regardless of signal mode):
  risk:halt=1  →  ALL strategy processing pauses immediately
  strategy disabled  →  that strategy skips signals
  strategy on cooldown  →  that strategy skips signals

Latency profiles (autonomous mode only, delay between scan iterations):
  fast:      0.1 s
  standard:  0.5 s
  relaxed:   2.0 s
"""

import asyncio

import httpx
import structlog
from redis.asyncio import Redis

from .config import Settings
from . import signal_consumer as tv_consumer
from .strategies.base import (
    LATENCY_DELAYS,
    get_strategy_state,
    is_halted,
    is_on_cooldown,
)
from .strategies.funding_arb import FundingArbStrategy
from .strategies.stat_arb import StatArbStrategy
from .strategies.swing import SwingStrategy

log = structlog.get_logger()

# Sleep interval when TV_SIGNAL_MODE=True (autonomous loops are idle)
_TV_MODE_IDLE_SLEEP = 30.0

# Sleep between iterations when disabled/halted in autonomous mode
_IDLE_SLEEP = 5.0


async def _funding_arb_loop(
    strategy: FundingArbStrategy,
    redis: Redis,
    client: httpx.AsyncClient,
    settings: Settings,
) -> None:
    """Funding arb autonomous scan loop — inactive in TV_SIGNAL_MODE."""
    name = "funding_arb"
    log.info("runner.strategy_loop_started", strategy=name, tv_mode=settings.TV_SIGNAL_MODE)

    while True:
        try:
            # In TV signal mode — this loop idles; the signal consumer handles trading
            if settings.TV_SIGNAL_MODE:
                await asyncio.sleep(_TV_MODE_IDLE_SLEEP)
                continue

            if await is_halted(redis):
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            state = await get_strategy_state(redis, name)
            if state.get("enabled") != "1":
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            if await is_on_cooldown(redis, name):
                log.debug("runner.strategy_on_cooldown", strategy=name)
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            await strategy.run_once(redis, state, client)

        except asyncio.CancelledError:
            log.info("runner.strategy_loop_cancelled", strategy=name)
            raise
        except Exception as exc:
            log.error("runner.strategy_iteration_error", strategy=name, error=str(exc))
            await asyncio.sleep(2.0)
            continue

        latency = state.get("latency_profile", "standard") if "state" in dir() else "standard"
        await asyncio.sleep(LATENCY_DELAYS.get(latency, 0.5))


async def _stat_arb_loop(
    strategy: StatArbStrategy,
    redis: Redis,
    settings: Settings,
) -> None:
    """Stat arb autonomous scan loop — inactive in TV_SIGNAL_MODE."""
    name = "stat_arb"
    log.info("runner.strategy_loop_started", strategy=name, tv_mode=settings.TV_SIGNAL_MODE)

    while True:
        try:
            if settings.TV_SIGNAL_MODE:
                await asyncio.sleep(_TV_MODE_IDLE_SLEEP)
                continue

            if await is_halted(redis):
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            state = await get_strategy_state(redis, name)
            if state.get("enabled") != "1":
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            if await is_on_cooldown(redis, name):
                log.debug("runner.strategy_on_cooldown", strategy=name)
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            await strategy.run_once(redis, state)

        except asyncio.CancelledError:
            log.info("runner.strategy_loop_cancelled", strategy=name)
            raise
        except Exception as exc:
            log.error("runner.strategy_iteration_error", strategy=name, error=str(exc))
            await asyncio.sleep(2.0)
            continue

        latency = state.get("latency_profile", "standard") if "state" in dir() else "standard"
        await asyncio.sleep(LATENCY_DELAYS.get(latency, 0.5))


async def _swing_loop(
    strategy: SwingStrategy,
    redis: Redis,
    settings: Settings,
) -> None:
    """Swing autonomous scan loop — no-op (swing is always TV-native)."""
    name = "swing"
    log.info(
        "runner.strategy_loop_started",
        strategy=name,
        note="swing is always TV-signal-native; this loop is a placeholder",
    )

    while True:
        try:
            # Swing has no autonomous scanning — run_once() is a no-op.
            # Idle at a long interval to keep the task alive.
            await asyncio.sleep(60.0)

        except asyncio.CancelledError:
            log.info("runner.strategy_loop_cancelled", strategy=name)
            raise
        except Exception as exc:
            log.error("runner.strategy_iteration_error", strategy=name, error=str(exc))
            await asyncio.sleep(2.0)


async def run(settings: Settings, redis: Redis) -> None:
    """
    Launch all strategy loops + the TradingView signal consumer.

    In TV_SIGNAL_MODE (default):
      - signal_consumer processes all trades
      - autonomous loops idle
    In autonomous mode:
      - both autonomous loops AND signal_consumer are active

    All tasks are cancelled cleanly on shutdown.
    """
    funding_arb = FundingArbStrategy(settings)
    stat_arb    = StatArbStrategy(settings)
    swing       = SwingStrategy(settings)

    log.info(
        "runner.starting",
        tv_signal_mode=settings.TV_SIGNAL_MODE,
        note="Bot will only trade on TradingView signals" if settings.TV_SIGNAL_MODE
             else "Bot running in autonomous mode + TradingView signals",
    )

    async with httpx.AsyncClient(
        headers={"User-Agent": "MeznaQuantFX/0.1"},
        follow_redirects=True,
    ) as http_client:

        tasks = [
            # TradingView signal consumer — always active
            asyncio.create_task(
                tv_consumer.run(
                    settings=settings,
                    redis=redis,
                    funding_arb=funding_arb,
                    stat_arb=stat_arb,
                    swing=swing,
                    http_client=http_client,
                ),
                name="strategy:tv_signal_consumer",
            ),
            # Autonomous scan loops — idle when TV_SIGNAL_MODE=True
            asyncio.create_task(
                _funding_arb_loop(funding_arb, redis, http_client, settings),
                name="strategy:funding_arb",
            ),
            asyncio.create_task(
                _stat_arb_loop(stat_arb, redis, settings),
                name="strategy:stat_arb",
            ),
            asyncio.create_task(
                _swing_loop(swing, redis, settings),
                name="strategy:swing",
            ),
        ]

        log.info("runner.all_tasks_launched", count=len(tasks))

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("runner.shutting_down")
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
