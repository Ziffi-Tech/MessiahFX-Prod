"""
RAG query endpoint.

POST /query — embed question → vector search → Claude synthesis → grounded answer.

The pipeline:
  1. Embed the question using OpenAI text-embedding-3-small
  2. Search Qdrant for top-k similar chunks (above MIN_SCORE threshold)
  3. Pass chunks + question to Claude Haiku synthesiser
  4. Return structured response with answer, sources, and metadata
"""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
import structlog

from ..embeddings import embed_query
from ..store import search
from ..synthesiser import synthesise
from ..config import Settings

log = structlog.get_logger()
router = APIRouter()


class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Natural language question to answer from the knowledge base",
    )
    category: str | None = Field(
        default=None,
        description="Optional category filter (e.g. 'strategy_note', 'market_research')",
    )
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="Override number of chunks to retrieve (default: from settings)",
    )


class SourceRef(BaseModel):
    title: str
    score: float
    chunk_index: int
    page_start: int | None = None
    page_end: int | None = None
    is_table: bool = False


class QueryResponse(BaseModel):
    question: str
    answer: str
    sources: list[SourceRef]
    chunks_used: int
    model: str
    timed_out: bool
    retrieval_count: int


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _get_client(request: Request):
    return getattr(request.app.state, "anthropic_client", None)


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Query the knowledge base and get a grounded answer",
)
async def query_knowledge_base(
    body: QueryRequest,
    request: Request,
    settings: Settings = Depends(_get_settings),
) -> QueryResponse:
    """
    Full RAG pipeline: embed → retrieve → synthesise.

    Returns a grounded answer citing specific documents.
    If context is insufficient, Claude will say so rather than hallucinating.
    """
    client = _get_client(request)

    # 1. Embed the question
    q_vector = await embed_query(body.question, settings)

    # 2. Retrieve similar chunks from Qdrant
    chunks = await search(
        query_vector=q_vector,
        settings=settings,
        category=body.category,
        top_k=body.top_k,
    )

    log.info(
        "query.retrieved",
        question_preview=body.question[:60],
        chunks_found=len(chunks),
        category=body.category,
    )

    # 3. Synthesise answer via Claude
    synthesis = await synthesise(
        question=body.question,
        chunks=chunks,
        settings=settings,
        client=client,
    )

    return QueryResponse(
        question=body.question,
        answer=synthesis["answer"],
        sources=[
            SourceRef(
                title=s.get("title", ""),
                score=s.get("score", 0.0),
                chunk_index=s.get("chunk_index", 0),
                page_start=s.get("page_start"),
                page_end=s.get("page_end"),
                is_table=s.get("is_table", False),
            )
            for s in synthesis["sources"]
        ],
        chunks_used=synthesis["chunks_used"],
        model=synthesis["model"],
        timed_out=synthesis["timed_out"],
        retrieval_count=len(chunks),
    )
