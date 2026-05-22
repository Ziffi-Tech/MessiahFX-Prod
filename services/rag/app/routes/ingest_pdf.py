"""
PDF file upload ingestion endpoint — quant-grade.

POST /ingest/pdf — multipart upload → extract → chunk → embed → upsert → analyse.

Pipeline:
  1. Validate file type and size
  2. Extract text + tables + page numbers (pdfplumber → pypdf fallback)
  3. Chunk with section-awareness and page metadata
  4. Embed chunks via OpenAI
  5. Upsert chunks to Qdrant (idempotent — re-upload safely overwrites)
  6. Run Claude Sonnet analysis to extract StrategyProfile
  7. Store StrategyProfile in Redis
  8. Return full ingest summary including strategy extraction status

The analysis pass (step 6) may take 30–180 seconds for large books.
The endpoint waits for it — the caller gets a complete response including
the extracted strategy name, type and confidence rating.

Usage (curl):
    curl -X POST http://localhost:8009/ingest/pdf \\
      -F "file=@turtle_trading.pdf" \\
      -F "source_id=books/turtle-trading" \\
      -F "title=Way of the Turtle" \\
      -F "category=strategy_book"
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
import structlog

from ..pdf_parser import extract_document
from ..chunker import prepare_chunks
from ..embeddings import embed_texts
from ..store import upsert_chunks
from ..analyser import analyse_document
from ..strategy_store import save_profile
from ..config import Settings

log = structlog.get_logger()
router = APIRouter()

_MAX_PDF_BYTES = 50 * 1024 * 1024   # 50 MB hard limit


class StrategyExtractionSummary(BaseModel):
    """Summary of the Claude Sonnet strategy extraction pass."""
    extracted: bool
    strategy_name: str
    strategy_type: str
    confidence: str          # "high" | "medium" | "low"
    entry_rules_count: int
    exit_rules_count: int
    key_principles_count: int
    extraction_complete: bool   # False if some sections timed out


class IngestPdfResponse(BaseModel):
    source_id: str
    title: str
    filename: str
    total_pages: int
    chars_extracted: int
    has_tables: bool
    parser_used: str            # "pdfplumber" or "pypdf"
    chunks_ingested: int
    category: str
    strategy: StrategyExtractionSummary


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _get_anthropic_client(request: Request):
    return getattr(request.app.state, "anthropic_client", None)


def _get_redis(request: Request):
    return getattr(request.app.state, "redis", None)


@router.post(
    "/ingest/pdf",
    response_model=IngestPdfResponse,
    summary="Upload a PDF, ingest it, and extract a trading strategy profile",
)
async def ingest_pdf(
    request: Request,
    file: UploadFile = File(..., description="PDF file to ingest"),
    source_id: str = Form(
        ...,
        min_length=1,
        max_length=200,
        description="Unique document identifier (e.g. 'books/turtle-trading')",
    ),
    title: str = Form(
        default="",
        max_length=300,
        description="Human-readable document title",
    ),
    category: str = Form(
        default="strategy_book",
        max_length=100,
        description="Category tag for filtered retrieval",
    ),
    chunk_size: int = Form(
        default=800,
        ge=200,
        le=4000,
        description="Chunk size in characters",
    ),
    chunk_overlap: int = Form(
        default=100,
        ge=0,
        le=500,
        description="Overlap between adjacent chunks",
    ),
    settings: Settings = Depends(_get_settings),
) -> IngestPdfResponse:
    """
    Full pipeline: upload PDF → extract → chunk → embed → upsert → analyse.

    Re-uploading the same source_id safely overwrites previous content in
    both Qdrant (chunks) and Redis (strategy profile).

    Large books (300+ pages) may take 2–4 minutes due to Claude Sonnet analysis.
    """
    anthropic_client = _get_anthropic_client(request)
    redis = _get_redis(request)

    # ── Validate file ──────────────────────────────────────────────────────────
    content_type = file.content_type or ""
    filename = file.filename or "upload.pdf"

    if not (content_type == "application/pdf" or filename.lower().endswith(".pdf")):
        raise HTTPException(
            status_code=422,
            detail=f"File must be a PDF. Received content_type='{content_type}'",
        )

    pdf_bytes = await file.read()

    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF too large: {len(pdf_bytes) / 1_048_576:.1f} MB (limit 50 MB)",
        )

    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    # ── Step 1: Extract text + tables + page numbers ───────────────────────────
    try:
        doc = extract_document(pdf_bytes, filename=filename)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    full_text = doc["full_text"]
    doc_title = title or filename

    log.info(
        "ingest_pdf.extracted",
        source_id=source_id,
        filename=filename,
        pages=doc["total_pages"],
        chars=len(full_text),
        has_tables=doc["has_tables"],
        parser=doc["parser_used"],
    )

    # ── Step 2: Chunk (section-aware, page metadata) ───────────────────────────
    chunks = prepare_chunks(
        full_text,
        source_id=source_id,
        title=doc_title,
        category=category,
        chunk_size=chunk_size,
        overlap=chunk_overlap,
    )

    if not chunks:
        raise HTTPException(
            status_code=422,
            detail="PDF yielded zero text chunks after splitting",
        )

    # ── Step 3: Embed ──────────────────────────────────────────────────────────
    texts = [c["text"] for c in chunks]
    vectors = await embed_texts(texts, settings)

    if len(vectors) != len(chunks):
        raise HTTPException(
            status_code=500,
            detail="Embedding count mismatch — check OpenAI API",
        )

    # ── Step 4: Upsert to Qdrant ───────────────────────────────────────────────
    upserted = await upsert_chunks(chunks, vectors, settings)

    log.info(
        "ingest_pdf.chunks_upserted",
        source_id=source_id,
        chunks=upserted,
    )

    # ── Step 5: Claude Sonnet strategy extraction ──────────────────────────────
    strategy_summary = StrategyExtractionSummary(
        extracted=False,
        strategy_name="",
        strategy_type="other",
        confidence="low",
        entry_rules_count=0,
        exit_rules_count=0,
        key_principles_count=0,
        extraction_complete=False,
    )

    if anthropic_client is not None and settings.anthropic_configured:
        try:
            profile = await analyse_document(
                full_text=full_text,
                source_id=source_id,
                title=doc_title,
                client=anthropic_client,
                settings=settings,
            )

            # ── Step 6: Persist profile to Redis ──────────────────────────────
            if redis is not None:
                await save_profile(redis, profile)
            else:
                log.warning(
                    "ingest_pdf.no_redis",
                    hint="Strategy profile extracted but not persisted — Redis unavailable",
                )

            strategy_summary = StrategyExtractionSummary(
                extracted=True,
                strategy_name=profile.get("strategy_name", ""),
                strategy_type=profile.get("strategy_type", "other"),
                confidence=profile.get("confidence", "low"),
                entry_rules_count=len(profile.get("entry_criteria", [])),
                exit_rules_count=len(profile.get("exit_criteria", [])),
                key_principles_count=len(profile.get("key_principles", [])),
                extraction_complete=profile.get("extraction_complete", False),
            )

        except Exception as exc:
            log.error(
                "ingest_pdf.analysis_failed",
                source_id=source_id,
                error=str(exc)[:150],
                hint="Chunks are in Qdrant but no strategy profile was extracted",
            )
    else:
        log.warning(
            "ingest_pdf.no_anthropic",
            source_id=source_id,
            hint="Set ANTHROPIC_API_KEY to enable strategy extraction",
        )

    log.info(
        "ingest_pdf.complete",
        source_id=source_id,
        filename=filename,
        chunks=upserted,
        strategy_extracted=strategy_summary.extracted,
        strategy_type=strategy_summary.strategy_type,
        confidence=strategy_summary.confidence,
    )

    return IngestPdfResponse(
        source_id=source_id,
        title=doc_title,
        filename=filename,
        total_pages=doc["total_pages"],
        chars_extracted=len(full_text),
        has_tables=doc["has_tables"],
        parser_used=doc["parser_used"],
        chunks_ingested=upserted,
        category=category,
        strategy=strategy_summary,
    )
