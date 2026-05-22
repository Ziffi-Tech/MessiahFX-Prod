"""
Qdrant vector store client.

Creates and manages the knowledge collection in Qdrant.
Each document chunk is stored as a point with:
  - id: UUID (deterministic from source_id + chunk_index)
  - vector: 1536-dim float (from OpenAI embeddings)
  - payload: {source_id, title, chunk_index, text, category, ingested_at}

Collection is created on startup if it doesn't exist.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from .config import Settings

log = structlog.get_logger()


def _chunk_id(source_id: str, chunk_index: int) -> str:
    """Deterministic UUID for a chunk — safe to re-ingest."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{chunk_index}"))


async def ensure_collection(settings: Settings) -> bool:
    """Create the Qdrant collection if it doesn't already exist. Returns True on success."""
    url = f"{settings.QDRANT_URL}/collections/{settings.QDRANT_COLLECTION}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Check if exists
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                log.info("qdrant.collection_exists", collection=settings.QDRANT_COLLECTION)
                return True
        except Exception as exc:
            log.error("qdrant.connection_failed", error=str(exc))
            return False

        # Create collection
        try:
            create_resp = await client.put(
                url,
                json={
                    "vectors": {
                        "size": settings.QDRANT_VECTOR_SIZE,
                        "distance": "Cosine",
                    }
                },
            )
            if create_resp.status_code in (200, 201):
                log.info(
                    "qdrant.collection_created",
                    collection=settings.QDRANT_COLLECTION,
                    vector_size=settings.QDRANT_VECTOR_SIZE,
                )
                return True
            else:
                log.error(
                    "qdrant.collection_create_failed",
                    status=create_resp.status_code,
                    body=create_resp.text[:200],
                )
                return False
        except Exception as exc:
            log.error("qdrant.create_error", error=str(exc))
            return False


async def upsert_chunks(
    chunks: list[dict],       # [{text, source_id, title, chunk_index, category}]
    vectors: list[list[float]],
    settings: Settings,
) -> int:
    """
    Upsert document chunks into Qdrant. Returns count of points upserted.

    Upsert is idempotent — re-ingesting the same source_id + chunk_index
    overwrites the existing point (via deterministic UUID).
    """
    points = []
    now = datetime.now(timezone.utc).isoformat()

    for chunk, vector in zip(chunks, vectors):
        point_id = _chunk_id(chunk["source_id"], chunk["chunk_index"])
        points.append({
            "id": point_id,
            "vector": vector,
            "payload": {
                "source_id": chunk["source_id"],
                "title": chunk.get("title", ""),
                "chunk_index": chunk["chunk_index"],
                "text": chunk["text"],
                "category": chunk.get("category", "general"),
                "page_start": chunk.get("page_start"),   # page metadata (None for non-PDF)
                "page_end": chunk.get("page_end"),
                "is_table": chunk.get("is_table", False),
                "ingested_at": now,
            },
        })

    if not points:
        return 0

    url = f"{settings.QDRANT_URL}/collections/{settings.QDRANT_COLLECTION}/points"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.put(url, json={"points": points})
            if resp.status_code == 200:
                log.info("qdrant.upserted", count=len(points))
                return len(points)
            else:
                log.error(
                    "qdrant.upsert_failed",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return 0
        except Exception as exc:
            log.error("qdrant.upsert_error", error=str(exc))
            return 0


async def search(
    query_vector: list[float],
    settings: Settings,
    category: str | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """
    Vector similarity search. Returns top_k chunks above MIN_SCORE threshold.

    Optional category filter narrows results to a specific document type
    (e.g., "strategy_note", "market_research", "trade_rationale").
    """
    k = top_k or settings.TOP_K
    url = f"{settings.QDRANT_URL}/collections/{settings.QDRANT_COLLECTION}/points/search"

    body: dict[str, Any] = {
        "vector": query_vector,
        "limit": k,
        "with_payload": True,
        "score_threshold": settings.MIN_SCORE,
    }
    if category:
        body["filter"] = {
            "must": [{"key": "category", "match": {"value": category}}]
        }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json=body)
            if resp.status_code != 200:
                log.error("qdrant.search_failed", status=resp.status_code)
                return []
            results = resp.json().get("result", [])
            return [
                {
                    "score": r["score"],
                    "text": r["payload"].get("text", ""),
                    "title": r["payload"].get("title", ""),
                    "source_id": r["payload"].get("source_id", ""),
                    "category": r["payload"].get("category", ""),
                    "chunk_index": r["payload"].get("chunk_index", 0),
                    "page_start": r["payload"].get("page_start"),
                    "page_end": r["payload"].get("page_end"),
                    "is_table": r["payload"].get("is_table", False),
                }
                for r in results
            ]
        except Exception as exc:
            log.error("qdrant.search_error", error=str(exc))
            return []


async def collection_stats(settings: Settings) -> dict:
    """Return Qdrant collection statistics."""
    url = f"{settings.QDRANT_URL}/collections/{settings.QDRANT_COLLECTION}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                info = resp.json().get("result", {})
                return {
                    "points_count": info.get("points_count", 0),
                    "indexed_vectors_count": info.get("indexed_vectors_count", 0),
                    "status": info.get("status", "unknown"),
                }
        except Exception:
            pass
    return {"points_count": -1, "status": "unreachable"}
