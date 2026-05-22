"""
RAG (Retrieval-Augmented Generation) service — quant-grade.

Provides a knowledge base for MeznaQuantFX backed by Qdrant vector storage
and Redis strategy profile persistence.

Startup sequence:
  1. Load settings from environment
  2. Ensure Qdrant collection exists (create on first run)
  3. Connect to Redis (strategy profile storage)
  4. Instantiate AsyncAnthropic client (synthesis + analysis)
  5. Register API routes

Endpoints:
  POST   /ingest/pdf           — upload PDF → extract → chunk → embed → analyse
  POST   /ingest               — ingest raw text chunks
  POST   /query                — embed → retrieve → synthesise grounded answer
  GET    /strategies           — list all extracted strategy profiles
  GET    /strategies/{id}      — full strategy profile for one document
  DELETE /strategies/{id}      — remove a strategy profile
  GET    /health/live          — liveness
  GET    /health/ready         — readiness (Qdrant + Redis reachable)
  GET    /health/stats         — Qdrant collection stats
  GET    /docs                 — Swagger UI
"""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

import anthropic
from redis.asyncio import Redis

from .config import settings as _settings
from mezna_shared.metrics import setup_metrics
from .store import ensure_collection
from .routes.health import router as health_router
from .routes.ingest import router as ingest_router
from .routes.ingest_pdf import router as ingest_pdf_router
from .routes.query import router as query_router
from .routes.strategy_profiles import router as strategy_profiles_router

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    app.state.settings = _settings

    log.info(
        "rag.startup",
        service=_settings.SERVICE_NAME,
        version=_settings.VERSION,
        qdrant=_settings.QDRANT_URL,
        collection=_settings.QDRANT_COLLECTION,
        openai_configured=_settings.openai_configured,
        anthropic_configured=_settings.anthropic_configured,
        analysis_model=_settings.ANALYSIS_MODEL,
        synthesis_model=_settings.SYNTHESIS_MODEL,
    )

    # ── Qdrant collection ─────────────────────────────────────────────────────
    ok = await ensure_collection(_settings)
    if ok:
        log.info("rag.collection_ready", collection=_settings.QDRANT_COLLECTION)
    else:
        log.warning(
            "rag.collection_unavailable",
            hint="Qdrant may not be running — ingest and query will fail",
        )

    # ── Redis (strategy profiles) ─────────────────────────────────────────────
    try:
        redis = Redis.from_url(_settings.REDIS_URL, decode_responses=False)
        await redis.ping()
        app.state.redis = redis
        log.info("rag.redis_ready", url=_settings.REDIS_URL)
    except Exception as exc:
        app.state.redis = None
        log.warning(
            "rag.redis_unavailable",
            error=str(exc)[:80],
            hint="Strategy profiles will not be persisted — check REDIS_URL",
        )

    # ── Anthropic client (synthesis + book analysis) ──────────────────────────
    if _settings.anthropic_configured:
        app.state.anthropic_client = anthropic.AsyncAnthropic(
            api_key=_settings.ANTHROPIC_API_KEY
        )
        log.info(
            "rag.anthropic_ready",
            synthesis_model=_settings.SYNTHESIS_MODEL,
            analysis_model=_settings.ANALYSIS_MODEL,
        )
    else:
        app.state.anthropic_client = None
        log.warning(
            "rag.anthropic_not_configured",
            hint=(
                "Set ANTHROPIC_API_KEY to enable answer synthesis and "
                "strategy extraction from uploaded books"
            ),
        )

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    if getattr(app.state, "anthropic_client", None) is not None:
        await app.state.anthropic_client.close()

    if getattr(app.state, "redis", None) is not None:
        await app.state.redis.aclose()

    log.info("rag.shutdown")


app = FastAPI(
    title="MeznaQuantFX RAG Service",
    description=(
        "Quantitative knowledge base with automatic strategy extraction. "
        "Upload trading books and research notes — the system extracts "
        "structured risk management frameworks and trading strategies. "
        "Query the knowledge base with natural language questions."
    ),
    version=_settings.VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Routes ────────────────────────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(ingest_router)
app.include_router(ingest_pdf_router)
app.include_router(query_router)
app.include_router(strategy_profiles_router)

setup_metrics(app, service_name=_settings.SERVICE_NAME)
