"""
Live OHLCV bar writer.

A long-lived background task (sibling to the feeds) that periodically resamples
the Redis tick cache into completed OHLCV candles and persists them to the
ohlcv_bars table via mezna_shared.ohlcv. This accumulates real history as the
system runs — the persisted-history prerequisite for DB-backed backtests and for
the directional bar-mode strategies to see more than the 500-tick live cache.

Design (mirrors the feed tasks):
  - Runs as one asyncio.Task; failures are caught and retried on the next cycle.
  - Only COMPLETED buckets are written (the still-forming current bucket is
    skipped), so a bar is never persisted half-built. Re-persisting an already
    complete bucket on a later cycle is idempotent (upsert is last-writer-wins,
    and the same ticks resample to the same bar).
  - volume = tick count (a liquidity proxy for quote feeds), source='live_ticks'.
  - Gated by BAR_WRITER_ENABLED; a no-op when off.
"""

import asyncio
import json
import time

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from mezna_shared.bars import ticks_to_ohlcv
from mezna_shared.ohlcv import upsert_bars, seconds_to_interval
from mezna_shared.redis_client import RedisKeys

from .config import Settings

log = structlog.get_logger()


async def _read_ticks(redis: Redis, venue: str, symbol: str, n: int) -> list[dict]:
    """Read up to N cached ticks (most recent first) as parsed dicts."""
    raw_list = await redis.lrange(RedisKeys.tick_cache(venue, symbol), 0, n - 1)
    ticks: list[dict] = []
    for raw in raw_list:
        try:
            ticks.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    return ticks


async def _flush_once(
    settings: Settings,
    redis: Redis,
    db_engine: AsyncEngine,
    interval_label: str,
) -> int:
    """Resample + persist completed bars for every target once. Returns rows written."""
    bar_seconds = settings.BAR_WRITER_BAR_SECONDS
    now = time.time()
    total = 0

    for venue, symbol in settings.bar_writer_targets:
        ticks = await _read_ticks(redis, venue, symbol, settings.TICK_CACHE_MAX_SIZE)
        if not ticks:
            continue
        bars = ticks_to_ohlcv(ticks, bar_seconds)  # oldest-first
        # Keep only buckets whose window has fully elapsed — drop the forming bar.
        completed = [b for b in bars if float(b["epoch"]) + bar_seconds <= now]
        if not completed:
            continue
        total += await upsert_bars(
            db_engine, venue, symbol, interval_label, completed, source="live_ticks"
        )

    if total:
        log.debug("bar_writer.flushed", bars=total, interval=interval_label)
    return total


async def run(settings: Settings, redis: Redis, db_engine: AsyncEngine) -> None:
    """
    Bar-writer loop. Flushes completed bars every BAR_WRITER_INTERVAL_SECONDS.

    Catches its own exceptions so a transient DB/Redis blip never kills the task;
    exits cleanly on cancellation at shutdown.
    """
    if not settings.BAR_WRITER_ENABLED:
        log.info("bar_writer.disabled")
        return

    interval_label = seconds_to_interval(settings.BAR_WRITER_BAR_SECONDS)
    targets = settings.bar_writer_targets
    log.info(
        "bar_writer.starting",
        interval=interval_label,
        flush_seconds=settings.BAR_WRITER_INTERVAL_SECONDS,
        targets=len(targets),
    )

    while True:
        try:
            await _flush_once(settings, redis, db_engine, interval_label)
        except asyncio.CancelledError:
            log.info("bar_writer.stopping")
            raise
        except Exception as exc:
            log.error("bar_writer.cycle_error", error=str(exc))
        await asyncio.sleep(settings.BAR_WRITER_INTERVAL_SECONDS)
