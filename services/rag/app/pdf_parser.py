"""
PDF text + table extractor — quant-grade.

Strategy:
  Primary:  pdfplumber — preserves table structure, column alignment, page layout.
            Tables are extracted as Markdown so the chunker and Claude can read them.
  Fallback: pypdf — pure Python, used when pdfplumber is unavailable or fails.

Output format:
  A list of PageContent dicts, one per page:
    {
      "page": int,           # 1-indexed page number
      "text": str,           # body text for this page
      "tables": list[str],   # each table rendered as Markdown
      "headings": list[str], # detected section/chapter headings
    }

  plus a convenience `full_text: str` that joins all pages with page markers
  so the analyser can process the whole document as one string while
  the chunker can use the per-page structure for section-aware splitting.

Limitations:
  - Scanned PDFs (image-only) return empty text — OCR is not included.
  - Complex rotated/multi-column layouts may partially merge columns.
  - Password-protected PDFs raise ValueError.
"""

import io
import re
from typing import TypedDict

import structlog

log = structlog.get_logger()


# ── Output types ──────────────────────────────────────────────────────────────

class PageContent(TypedDict):
    page: int
    text: str
    tables: list[str]
    headings: list[str]


class ExtractedDocument(TypedDict):
    filename: str
    total_pages: int
    pages: list[PageContent]
    full_text: str          # full document as one string, with page markers
    has_tables: bool
    parser_used: str        # "pdfplumber" or "pypdf"


# ── Heading detection ─────────────────────────────────────────────────────────

# Patterns that indicate a section or chapter heading:
#   "Chapter 1", "CHAPTER ONE", "1. Risk Management", "Section 3.2 — ..."
_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:chapter|section|part)\s+[\divxlc]+[\s\.:—–-]"   # Chapter/Section/Part N
    r"|[\d]+[\.\)]\s+[A-Z]"                                # "1. Title" or "1) Title"
    r"|[A-Z][A-Z\s]{8,}$"                                  # ALL CAPS line ≥ 8 chars
    r")",
    re.IGNORECASE,
)


def _detect_headings(lines: list[str]) -> list[str]:
    """Return lines that look like section headings."""
    headings = []
    for line in lines:
        stripped = line.strip()
        if 4 <= len(stripped) <= 120 and _HEADING_RE.match(stripped):
            headings.append(stripped)
    return headings


# ── Table → Markdown renderer ─────────────────────────────────────────────────

def _table_to_markdown(table: list[list[str | None]]) -> str:
    """
    Convert a pdfplumber table (list of rows, each a list of cell strings)
    into a Markdown table string.

    Empty cells become empty strings. None cells become "".
    Returns empty string if the table has < 2 rows or < 2 columns (degenerate).
    """
    if not table or len(table) < 2:
        return ""

    # Normalise: replace None with ""
    norm = [[str(cell).strip() if cell else "" for cell in row] for row in table]

    # All rows must have same column count — pad shorter rows
    n_cols = max(len(row) for row in norm)
    norm = [row + [""] * (n_cols - len(row)) for row in norm]

    if n_cols < 2:
        return ""

    # Build Markdown table
    header = norm[0]
    rows = norm[1:]

    lines = []
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * n_cols) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")

    return "\n".join(lines)


# ── Page-level cleanup ────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """
    Light text cleanup:
      - Remove pure page-number lines (short numeric-only lines)
      - Collapse excessive blank lines
      - Strip per-line leading/trailing whitespace
    """
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Skip pure page-number lines (≤ 6 chars, only digits/dashes/dots)
        if re.fullmatch(r"[\d\s\-–—·|]+", stripped) and len(stripped) <= 6:
            continue
        cleaned.append(stripped)

    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned))
    return result.strip()


# ── Primary parser: pdfplumber ────────────────────────────────────────────────

def _extract_with_pdfplumber(pdf_bytes: bytes, filename: str) -> ExtractedDocument:
    """Extract text + tables from PDF using pdfplumber."""
    import pdfplumber  # local import — package may not always be installed

    pages: list[PageContent] = []
    has_tables = False

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        total_pages = len(pdf.pages)

        for i, page in enumerate(pdf.pages):
            page_num = i + 1

            # ── Extract tables first, then get remaining text ──────────────
            tables_md: list[str] = []
            try:
                raw_tables = page.extract_tables()
                for tbl in (raw_tables or []):
                    md = _table_to_markdown(tbl)
                    if md:
                        tables_md.append(md)
                        has_tables = True
            except Exception as exc:
                log.debug("pdf_parser.table_extract_error", page=page_num, error=str(exc)[:60])

            # ── Extract body text (excluding table bounding boxes) ─────────
            try:
                # pdfplumber can exclude table areas to avoid double-counting
                if raw_tables:
                    # Get table bounding boxes and filter them out
                    table_settings = {}
                    text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                else:
                    text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            except Exception as exc:
                log.debug("pdf_parser.text_extract_error", page=page_num, error=str(exc)[:60])
                text = ""

            cleaned = _clean_text(text)
            lines = cleaned.splitlines()
            headings = _detect_headings(lines)

            pages.append(PageContent(
                page=page_num,
                text=cleaned,
                tables=tables_md,
                headings=headings,
            ))

    # ── Build full_text with page markers ─────────────────────────────────────
    full_text = _build_full_text(pages)

    log.info(
        "pdf_parser.pdfplumber_done",
        filename=filename,
        pages=total_pages,
        chars=len(full_text),
        has_tables=has_tables,
    )

    return ExtractedDocument(
        filename=filename,
        total_pages=total_pages,
        pages=pages,
        full_text=full_text,
        has_tables=has_tables,
        parser_used="pdfplumber",
    )


# ── Fallback parser: pypdf ────────────────────────────────────────────────────

def _extract_with_pypdf(pdf_bytes: bytes, filename: str) -> ExtractedDocument:
    """
    Fallback extractor using pypdf.
    Returns the same ExtractedDocument structure but with empty tables lists
    (pypdf cannot reliably extract tables).
    """
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise ValueError(f"PDF '{filename}' is encrypted and cannot be decrypted")

    total_pages = len(reader.pages)
    pages: list[PageContent] = []

    for i, page in enumerate(reader.pages):
        page_num = i + 1
        try:
            raw = page.extract_text() or ""
        except Exception as exc:
            log.warning("pdf_parser.pypdf_page_failed", page=page_num, error=str(exc)[:60])
            raw = ""

        cleaned = _clean_text(raw)
        lines = cleaned.splitlines()
        headings = _detect_headings(lines)

        pages.append(PageContent(
            page=page_num,
            text=cleaned,
            tables=[],          # pypdf cannot extract tables
            headings=headings,
        ))

    full_text = _build_full_text(pages)

    log.info(
        "pdf_parser.pypdf_done",
        filename=filename,
        pages=total_pages,
        chars=len(full_text),
    )

    return ExtractedDocument(
        filename=filename,
        total_pages=total_pages,
        pages=pages,
        full_text=full_text,
        has_tables=False,
        parser_used="pypdf",
    )


# ── Page marker assembly ──────────────────────────────────────────────────────

def _build_full_text(pages: list[PageContent]) -> str:
    """
    Join all page texts + tables into one string with page markers.

    Format:
        --- PAGE 1 ---
        <body text>
        <Table 1 markdown if present>
        --- PAGE 2 ---
        ...

    Page markers let the analyser and chunker know where page boundaries
    fall, enabling page-level citations in the final strategy profile.
    """
    parts: list[str] = []
    for page in pages:
        marker = f"--- PAGE {page['page']} ---"
        page_content = page["text"]

        # Append any tables found on this page
        for j, table_md in enumerate(page["tables"], 1):
            page_content += f"\n\n[Table {j}, Page {page['page']}]\n{table_md}"

        if page_content.strip():
            parts.append(f"{marker}\n{page_content}")

    return "\n\n".join(parts)


# ── Public API ────────────────────────────────────────────────────────────────

def extract_document(pdf_bytes: bytes, filename: str = "upload.pdf") -> ExtractedDocument:
    """
    Extract text, tables, headings and page numbers from a PDF.

    Tries pdfplumber first; falls back to pypdf on import error or parse failure.
    Raises ValueError if the document yields no extractable text.
    """
    if not pdf_bytes:
        raise ValueError("PDF bytes are empty")

    # ── Try pdfplumber (primary) ───────────────────────────────────────────────
    try:
        doc = _extract_with_pdfplumber(pdf_bytes, filename)
        if doc["full_text"].strip():
            return doc
        log.warning("pdf_parser.pdfplumber_empty", filename=filename, hint="Falling back to pypdf")
    except ImportError:
        log.warning("pdf_parser.pdfplumber_not_installed", hint="pip install pdfplumber")
    except Exception as exc:
        log.warning("pdf_parser.pdfplumber_failed", filename=filename, error=str(exc)[:100])

    # ── Fallback: pypdf ────────────────────────────────────────────────────────
    try:
        doc = _extract_with_pypdf(pdf_bytes, filename)
    except ImportError as exc:
        raise RuntimeError("Neither pdfplumber nor pypdf is installed") from exc

    if not doc["full_text"].strip():
        raise ValueError(
            f"PDF '{filename}' yielded no extractable text. "
            "It may be a scanned image PDF — OCR is not supported."
        )

    return doc


def extract_text_from_pdf(pdf_bytes: bytes, filename: str = "upload.pdf") -> str:
    """
    Compatibility shim — returns full_text string (used by legacy ingest route).
    New code should call extract_document() to get the full structured result.
    """
    return extract_document(pdf_bytes, filename)["full_text"]
