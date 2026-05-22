"""
AI filter service — Claude Haiku soft gate.

Phase 3 responsibilities:
  - Consume signals from signals:opportunities Redis Stream (consumer group)
  - Score each opportunity with Claude Haiku (800ms timeout)
  - Enrich the payload with AI score, reason, and timeout flag
  - Forward ALL signals to signals:approved (advisory only — never blocks)

Advisory-only contract:
  This service NEVER drops a signal. Even a score of 0 or a timeout results
  in the signal reaching the risk engine. The risk engine has final authority.
  The AI layer is a second opinion — not a gate.
"""

import asyncio
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

import anthropic
import httpx

from mezna_shared.logging_config import setup_logging
from mezna_shared.db import get_engine, check_db_connection, dispose_engine
from mezna_shared.redis_client import get_redis, close_redis

from .config import settings
from .routes import health
from .routes import analyse
from .routes import risk_narrative
from .routes import regime
from .routes import digest
from .routes import research_agent
from .routes import trade_agent
from .routes import portfolio_agent
from .routes import portfolio_sizing
from . import consumer
from .news_sentinel import run_news_sentinel

setup_logging(
    service_name=settings.SERVICE_NAME,
    log_level=settings.LOG_LEVEL,
    debug=settings.DEBUG,
)
log = structlog.get_logger()

_consumer_task: asyncio.Task | None = None
_sentinel_task: asyncio.Task | None = None


def _on_consumer_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("consumer.exited_unexpectedly", error=str(exc))


def _on_sentinel_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("news_sentinel.exited_unexpectedly", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _consumer_task, _sentinel_task

    log.info("service.starting", service=settings.SERVICE_NAME, version=settings.VERSION)

    app.state.db_engine = get_engine(settings.DATABASE_URL)
    app.state.redis = await get_redis(settings.REDIS_URL)

    db_ok = await check_db_connection(app.state.db_engine)
    if not db_ok:
        raise RuntimeError("Database unreachable at startup")

    try:
        await app.state.redis.ping()
    except Exception as exc:
        raise RuntimeError("Redis unreachable at startup") from exc

    # ── HTTP client for agent tool calls to other services ────────────────────
    app.state.agent_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        headers={"User-Agent": f"mezna-ai-filter/{settings.VERSION}"},
    )
    log.info(
        "agent.http_client_ready",
        journal_url=settings.JOURNAL_URL,
        backtest_url=settings.BACKTEST_URL,
        rag_url=settings.RAG_URL,
        risk_url=settings.RISK_URL,
    )

    # ── Anthropic client (shared for consumer + analysis endpoint) ────────────
    if settings.ai_configured:
        app.state.anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY
        )
        log.info(
            "ai_filter.client_ready",
            scoring_model=settings.AI_SCORING_MODEL,
            analysis_model=settings.AI_ANALYSIS_MODEL,
        )
    else:
        app.state.anthropic_client = None
        log.warning(
            "ai_filter.unconfigured",
            hint="Set ANTHROPIC_API_KEY — signals will pass through unscored until then",
        )

    # ── Launch consumer ────────────────────────────────────────────────────────
    _consumer_task = asyncio.create_task(
        consumer.run(settings, app.state.redis),
        name="ai_filter_consumer",
    )
    _consumer_task.add_done_callback(_on_consumer_done)

    # ── Launch news sentiment background task ──────────────────────────────────
    _sentinel_task = asyncio.create_task(
        run_news_sentinel(settings, app.state.redis),
        name="news_sentinel",
    )
    _sentinel_task.add_done_callback(_on_sentinel_done)

    log.info(
        "service.ready",
        service=settings.SERVICE_NAME,
        ai_configured=settings.ai_configured,
        scoring_model=settings.AI_SCORING_MODEL,
        timeout_ms=settings.AI_TIMEOUT_MS,
    )

    yield  # ── Service is running ─────────────────────────────────────────────

    log.info("service.stopping", service=settings.SERVICE_NAME)

    if _consumer_task and not _consumer_task.done():
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass

    if _sentinel_task and not _sentinel_task.done():
        _sentinel_task.cancel()
        try:
            await _sentinel_task
        except asyncio.CancelledError:
            pass

    if app.state.anthropic_client is not None:
        await app.state.anthropic_client.close()

    await app.state.agent_http_client.aclose()

    await close_redis()
    await dispose_engine()
    log.info("service.stopped", service=settings.SERVICE_NAME)


app = FastAPI(
    title="MeznaQuantFX — AI Filter",
    description="Claude Haiku soft gate. Advisory only. 800ms timeout. Never blocks.",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",   # Always on — operators use /ai/analyse interactively
    redoc_url=None,
)

app.include_router(health.router,          prefix="/health", tags=["health"])
app.include_router(analyse.router,         prefix="/ai",     tags=["deep-analysis"])
app.include_router(risk_narrative.router,  prefix="/ai",     tags=["risk-narrative"])
app.include_router(regime.router,          prefix="/ai",     tags=["regime"])
app.include_router(digest.router,          prefix="/ai",     tags=["digest"])
# ── Agents (agentic multi-step tool-use loops) ─────────────────────────────
app.include_router(research_agent.router,  prefix="/ai",     tags=["agents"])
app.include_router(trade_agent.router,     prefix="/ai",     tags=["agents"])
app.include_router(portfolio_agent.router, prefix="/ai",     tags=["agents"])
app.include_router(portfolio_sizing.router, prefix="/ai",   tags=["portfolio"])

from mezna_shared.metrics import setup_metrics
setup_metrics(app, service_name=settings.SERVICE_NAME)
