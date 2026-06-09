"""
Strategy runner — orchestrates all strategy tasks.

Strategies (6 total):
  funding_arb          — funding rate delta capture (regime-neutral)
  stat_arb             — spot/perp z-score divergence (ranging/high-vol)
  swing                — TradingView webhook-native (trending)
  breakout             — ATR volatility breakout (trending)
  mean_reversion_scalp — RSI + BB confluence (ranging/low-vol)
  momentum             — multi-timeframe ROC continuation (trending)

Signal modes (TV_SIGNAL_MODE):
  True  (default) — only trades on TradingView webhooks; autonomous loops idle
  False           — autonomous scanning active + TradingView signals

Strategy rotation:
  Each iteration records outcomes to the rotation engine.
  When a strategy hits ROTATION_CONSECUTIVE_LOSS_THRESHOLD consecutive losses,
  the rotation engine flags it degraded and prefers the regime-best alternative.
  Operators retain full control — rotation is advisory, not mandatory.
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
from .strategies.breakout import BreakoutStrategy
from .strategies.mean_reversion_scalp import MeanReversionScalpStrategy
from .strategies.momentum import MomentumStrategy
from .strategies import regime_detector
from .strategies import edge_monitor

log = structlog.get_logger()

_TV_MODE_IDLE_SLEEP = 30.0
_IDLE_SLEEP = 5.0


async def _generic_loop(
    strategy_name: str,
    strategy,
    redis: Redis,
    settings: Settings,
    extra_kwargs: dict | None = None,
) -> None:
    """
    Generic strategy scan loop used by all scan-based strategies.
    Inactive when TV_SIGNAL_MODE=True.
    Respects kill switch, strategy toggle, and cooldown.
    """
    log.info("runner.strategy_loop_started", strategy=strategy_name,
             tv_mode=settings.TV_SIGNAL_MODE)
    state: dict = {}

    while True:
        try:
            if settings.TV_SIGNAL_MODE:
                await asyncio.sleep(_TV_MODE_IDLE_SLEEP)
                continue

            if await is_halted(redis):
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            state = await get_strategy_state(redis, strategy_name)
            if state.get("enabled") != "1":
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            if await is_on_cooldown(redis, strategy_name):
                log.debug("runner.strategy_on_cooldown", strategy=strategy_name)
                await asyncio.sleep(_IDLE_SLEEP)
                continue

            kwargs = extra_kwargs or {}
            await strategy.run_once(redis, state, **kwargs)

        except asyncio.CancelledError:
            log.info("runner.strategy_loop_cancelled", strategy=strategy_name)
            raise
        except Exception as exc:
            log.error("runner.strategy_iteration_error",
                      strategy=strategy_name, error=str(exc))
            await asyncio.sleep(2.0)
            continue

        await asyncio.sleep(LATENCY_DELAYS.get(state.get("latency_profile", "standard"), 0.5))


async def _swing_loop(strategy: SwingStrategy, redis: Redis) -> None:
    """Swing is always TV-native — this is just a keepalive placeholder."""
    log.info("runner.strategy_loop_started", strategy="swing",
             note="swing is TV-signal-native; this loop is a placeholder")
    while True:
        try:
            await asyncio.sleep(60.0)
        except asyncio.CancelledError:
            log.info("runner.strategy_loop_cancelled", strategy="swing")
            raise
        except Exception as exc:
            log.error("runner.strategy_iteration_error", strategy="swing", error=str(exc))
            await asyncio.sleep(2.0)


_EDGE_STATUS_INTERVAL = 300.0  # Log edge status every 5 minutes


async def _edge_status_loop(redis: Redis, settings: Settings) -> None:
    """
    Periodically log the edge/win-rate status of all strategies.
    Does not alter any state — pure observability.
    """
    strategies = [
        "funding_arb", "stat_arb", "swing",
        "breakout", "mean_reversion_scalp", "momentum",
    ]
    while True:
        try:
            await asyncio.sleep(_EDGE_STATUS_INTERVAL)
            status = await edge_monitor.get_all_status(redis, strategies)
            for name, s in status["strategies"].items():
                if s["win_rate"] is not None:
                    log.info(
                        "runner.edge_status",
                        strategy=name,
                        win_rate=s["win_rate"],
                        window_size=s["window_size"],
                        decayed=s["decayed"],
                    )
        except asyncio.CancelledError:
            log.info("runner.edge_status_loop_cancelled")
            raise
        except Exception as exc:
            log.error("runner.edge_status_loop_error", error=str(exc))


async def run(settings: Settings, redis: Redis) -> None:
    """
    Launch all 6 strategy loops + the TradingView signal consumer.
    Also runs:
      - local regime detector (fills ai:regime:current when ai-filter is cold)
      - edge decay status logger (every 5 min)
    """
    funding_arb     = FundingArbStrategy(settings)
    stat_arb        = StatArbStrategy(settings)
    swing           = SwingStrategy(settings)
    breakout        = BreakoutStrategy(settings)
    mean_reversion  = MeanReversionScalpStrategy(settings)
    momentum        = MomentumStrategy(settings)

    log.info(
        "runner.starting",
        strategies=6,
        tv_signal_mode=settings.TV_SIGNAL_MODE,
        rotation_threshold=settings.ROTATION_CONSECUTIVE_LOSS_THRESHOLD,
        min_rr_ratio=settings.STRATEGY_MIN_RR_RATIO,
        regime_detector=settings.REGIME_DETECTOR_ENABLED,
    )

    async with httpx.AsyncClient(
        headers={"User-Agent": "MeznaQuantFX/0.1"},
        follow_redirects=True,
    ) as http_client:

        regime_tasks = []
        if settings.REGIME_DETECTOR_ENABLED:
            regime_tasks.append(
                asyncio.create_task(
                    regime_detector.run(
                        redis, settings.REGIME_DETECTOR_INTERVAL_SECONDS
                    ),
                    name="strategy:regime_detector",
                )
            )

        tasks = [
            *regime_tasks,
            # Edge decay status logger
            asyncio.create_task(
                _edge_status_loop(redis, settings),
                name="strategy:edge_status",
            ),
            # TV signal consumer — always active regardless of TV_SIGNAL_MODE
            asyncio.create_task(
                tv_consumer.run(
                    settings=settings,
                    redis=redis,
                    funding_arb=funding_arb,
                    stat_arb=stat_arb,
                    swing=swing,
                    breakout=breakout,
                    mean_reversion=mean_reversion,
                    momentum=momentum,
                    http_client=http_client,
                ),
                name="strategy:tv_signal_consumer",
            ),
            # Autonomous scan loops (idle in TV_SIGNAL_MODE)
            asyncio.create_task(
                _generic_loop("funding_arb", funding_arb, redis, settings,
                              extra_kwargs={"client": http_client}),
                name="strategy:funding_arb",
            ),
            asyncio.create_task(
                _generic_loop("stat_arb", stat_arb, redis, settings),
                name="strategy:stat_arb",
            ),
            asyncio.create_task(
                _swing_loop(swing, redis),
                name="strategy:swing",
            ),
            asyncio.create_task(
                _generic_loop("breakout", breakout, redis, settings),
                name="strategy:breakout",
            ),
            asyncio.create_task(
                _generic_loop("mean_reversion_scalp", mean_reversion, redis, settings),
                name="strategy:mean_reversion_scalp",
            ),
            asyncio.create_task(
                _generic_loop("momentum", momentum, redis, settings),
                name="strategy:momentum",
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
