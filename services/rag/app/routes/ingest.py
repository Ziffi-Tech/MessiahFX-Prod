"""
Document ingestion endpoint.

POST /ingest — accepts raw text + metadata, chunks it, embeds it, upserts to Qdrant.

Idempotent: re-ingesting the same source_id replaces all its chunks (deterministic UUIDs).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
import structlog

from ..chunker import prepare_chunks
from ..embeddings import embed_texts
from ..store import upsert_chunks
from ..config import Settings

log = structlog.get_logger()
router = APIRouter()


class IngestRequest(BaseModel):
    source_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Unique document identifier (e.g. 'research/btc-arb-2024')",
    )
    title: str = Field(
        default="",
        max_length=300,
        description="Human-readable document title",
    )
    text: str = Field(
        ...,
        min_length=1,
        description="Raw document text to embed and index",
    )
    category: str = Field(
        default="general",
        max_length=100,
        description="Document category for filtered retrieval (e.g. 'strategy_note', 'market_research')",
    )
    chunk_size: int | None = Field(
        default=None,
        ge=200,
        le=4000,
        description="Override chunk_size in characters (default: from settings)",
    )
    chunk_overlap: int | None = Field(
        default=None,
        ge=0,
        le=500,
        description="Override chunk overlap in characters (default: from settings)",
    )


class IngestResponse(BaseModel):
    source_id: str
    title: str
    chunks_ingested: int
    category: str


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.post(
    "/ingest",
    response_model=IngestResponse,
    summary="Ingest a document into the knowledge base",
)
async def ingest_document(
    body: IngestRequest,
    request: Request,
    settings: Settings = Depends(_get_settings),
) -> IngestResponse:
    """
    Chunk → embed → upsert pipeline.

    Re-ingesting the same source_id is safe: chunk UUIDs are deterministic,
    so Qdrant upsert overwrites the existing points in place.
    """
    chunk_size = body.chunk_size or settings.CHUNK_SIZE
    overlap = body.chunk_overlap or settings.CHUNK_OVERLAP

    # 1. Chunk the text
    chunks = prepare_chunks(
        body.text,
        source_id=body.source_id,
        title=body.title,
        category=body.category,
        chunk_size=chunk_size,
        overlap=overlap,
    )

    if not chunks:
        raise HTTPException(status_code=422, detail="Document produced zero chunks after splitting")

    # 2. Embed all chunks
    texts = [c["text"] for c in chunks]
    vectors = await embed_texts(texts, settings)

    if len(vectors) != len(chunks):
        log.error(
            "ingest.vector_count_mismatch",
            chunks=len(chunks),
            vectors=len(vectors),
        )
        raise HTTPException(status_code=500, detail="Embedding count mismatch — check OpenAI API")

    # 3. Upsert into Qdrant
    upserted = await upsert_chunks(chunks, vectors, settings)

    log.info(
        "ingest.complete",
        source_id=body.source_id,
        title=body.title,
        category=body.category,
        chunks=len(chunks),
        upserted=upserted,
    )

    return IngestResponse(
        source_id=body.source_id,
        title=body.title,
        chunks_ingested=upserted,
        category=body.category,
    )
