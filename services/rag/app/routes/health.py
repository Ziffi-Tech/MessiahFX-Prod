"""
Health endpoints for the RAG service.

GET /health/live  — liveness: service is up and running
GET /health/ready — readiness: Qdrant reachable + collection exists
GET /health/stats — collection statistics (point count, index status)
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..store import collection_stats, ensure_collection
from ..config import Settings

router = APIRouter()


class LiveResponse(BaseModel):
    status: str
    service: str
    version: str


class ReadyResponse(BaseModel):
    status: str
    qdrant: str
    collection: str
    openai_configured: bool
    anthropic_configured: bool


class StatsResponse(BaseModel):
    points_count: int
    indexed_vectors_count: int | None
    status: str
    collection: str


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/health/live", response_model=LiveResponse)
async def liveness(request: Request) -> LiveResponse:
    settings = _get_settings(request)
    return LiveResponse(
        status="ok",
        service=settings.SERVICE_NAME,
        version=settings.VERSION,
    )


@router.get("/health/ready", response_model=ReadyResponse)
async def readiness(request: Request) -> ReadyResponse:
    settings = _get_settings(request)

    # Check Qdrant connectivity by fetching stats
    stats = await collection_stats(settings)
    qdrant_ok = stats.get("status") not in ("unreachable", "unknown", -1)
    collection_status = stats.get("status", "unknown")

    return ReadyResponse(
        status="ok" if qdrant_ok else "degraded",
        qdrant="ok" if qdrant_ok else "unreachable",
        collection=collection_status,
        openai_configured=settings.openai_configured,
        anthropic_configured=settings.anthropic_configured,
    )


@router.get("/health/stats", response_model=StatsResponse)
async def stats(request: Request) -> StatsResponse:
    settings = _get_settings(request)
    info = await collection_stats(settings)
    return StatsResponse(
        points_count=info.get("points_count", -1),
        indexed_vectors_count=info.get("indexed_vectors_count"),
        status=info.get("status", "unknown"),
        collection=settings.QDRANT_COLLECTION,
    )
