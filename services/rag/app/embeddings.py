"""
Embedding client — wraps OpenAI text-embedding API.

Converts text chunks into dense vectors for storage in Qdrant.
Uses text-embedding-3-small (1536 dimensions) by default.

Batching:
  OpenAI allows up to 2048 inputs per request.
  We batch in groups of EMBEDDING_BATCH_SIZE (default 100).

Fallback:
  If OPENAI_API_KEY is not set, returns a zero vector of the correct
  dimension so the system still starts (ingest will be a no-op).
"""

import httpx
import structlog

from .config import Settings

log = structlog.get_logger()

_EMBED_URL = "https://api.openai.com/v1/embeddings"


async def embed_texts(
    texts: list[str],
    settings: Settings,
) -> list[list[float]]:
    """
    Return a list of embedding vectors (one per input text).

    Returns zero vectors if OpenAI is not configured.
    """
    if not settings.openai_configured:
        log.warning("embeddings.no_api_key", hint="Set OPENAI_API_KEY to enable real embeddings")
        return [[0.0] * settings.QDRANT_VECTOR_SIZE for _ in texts]

    all_vectors: list[list[float]] = []
    batch_size = settings.EMBEDDING_BATCH_SIZE

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i: i + batch_size]
            try:
                resp = await client.post(
                    _EMBED_URL,
                    headers={
                        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={"model": settings.EMBEDDING_MODEL, "input": batch},
                )
                resp.raise_for_status()
                data = resp.json()
                # data["data"] is sorted by index
                batch_vecs = [item["embedding"] for item in sorted(data["data"], key=lambda x: x["index"])]
                all_vectors.extend(batch_vecs)
                log.debug("embeddings.batch_done", batch_start=i, count=len(batch))
            except Exception as exc:
                log.error("embeddings.batch_failed", batch_start=i, error=str(exc))
                # Return zero vectors for failed batch
                all_vectors.extend([[0.0] * settings.QDRANT_VECTOR_SIZE] * len(batch))

    return all_vectors


async def embed_query(query: str, settings: Settings) -> list[float]:
    """Embed a single query string for retrieval."""
    vecs = await embed_texts([query], settings)
    return vecs[0] if vecs else [0.0] * settings.QDRANT_VECTOR_SIZE
