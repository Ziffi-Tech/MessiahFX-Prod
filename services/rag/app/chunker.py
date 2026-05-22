"""
Document chunker — section-aware, finance-grade.

Upgrades over the original character-count chunker:

1. Section-header detection — when a heading is found, start a new chunk
   immediately so context from one section never bleeds into another.

2. Table preservation — Markdown table blocks (produced by the PDF parser)
   are kept intact; they are never split mid-row.

3. Page metadata — every chunk carries the page number(s) it was drawn from,
   enabling page-level citations in answers.

4. Minimum chunk gate — chunks below MIN_CHARS characters are merged with
   the previous chunk to avoid embedding noise from tiny fragments.
"""

import re

# Minimum content a chunk must have before it is stored as a separate point.
_MIN_CHUNK_CHARS = 120

# Pattern that matches the page markers inserted by pdf_parser._build_full_text
_PAGE_MARKER_RE = re.compile(r"^---\s*PAGE\s+(\d+)\s*---$", re.MULTILINE)

# Pattern that matches the start of a Markdown table row
_TABLE_ROW_RE = re.compile(r"^\|")

# Heading patterns (same family as pdf_parser, repeated here to avoid coupling)
_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:chapter|section|part)\s+[\divxlc]+[\s\.:—–-]"
    r"|[\d]+[\.\)]\s+[A-Z]"
    r"|[A-Z][A-Z\s]{8,}$"
    r")",
    re.IGNORECASE,
)


# ── Page tracker ──────────────────────────────────────────────────────────────

def _build_page_index(text: str) -> list[tuple[int, int]]:
    """
    Return a list of (char_offset, page_number) pairs from a text that
    contains '--- PAGE N ---' markers.

    Used to map a character offset → page number in O(log n) via bisect.
    """
    result: list[tuple[int, int]] = [(0, 1)]   # default page 1 before first marker
    for m in _PAGE_MARKER_RE.finditer(text):
        result.append((m.start(), int(m.group(1))))
    return result


def _offset_to_page(page_index: list[tuple[int, int]], offset: int) -> int:
    """Binary-search page_index to find which page a character offset falls on."""
    lo, hi = 0, len(page_index) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if page_index[mid][0] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return page_index[lo][1]


# ── Table block extractor ─────────────────────────────────────────────────────

def _is_table_line(line: str) -> bool:
    return bool(_TABLE_ROW_RE.match(line.strip()))


def _extract_table_blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    """
    Identify contiguous runs of Markdown table lines.
    Returns list of (start_line, end_line, table_text) — indices are
    into the `lines` list.  Used so the splitter can skip over tables.
    """
    blocks = []
    i = 0
    while i < len(lines):
        if _is_table_line(lines[i]):
            j = i
            while j < len(lines) and (_is_table_line(lines[j]) or lines[j].strip() == ""):
                j += 1
            table_text = "\n".join(lines[i:j]).strip()
            if table_text:
                blocks.append((i, j, table_text))
            i = j
        else:
            i += 1
    return blocks


# ── Core splitter ─────────────────────────────────────────────────────────────

def _find_sentence_boundary(text: str, search_from: int, search_to: int) -> int:
    """Find the last clean sentence boundary in text[search_from:search_to]."""
    slice_ = text[search_from:search_to]
    for delimiter in ("\n\n", "\n", ". "):
        idx = slice_.rfind(delimiter)
        if idx != -1:
            return search_from + idx + len(delimiter)
    return -1


def _split_text_block(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    Split a plain text block (no table markers) into overlapping chunks.
    Respects sentence boundaries within a ±200 char search window.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:].strip())
            break
        search_start = max(start + chunk_size - 200, start)
        boundary = _find_sentence_boundary(text, search_start, end)
        if boundary == -1:
            boundary = end
        chunk = text[start:boundary].strip()
        if chunk:
            chunks.append(chunk)
        start = max(boundary - overlap, start + 1)

    return [c for c in chunks if c]


# ── Section-aware splitter ────────────────────────────────────────────────────

def _split_into_sections(text: str) -> list[str]:
    """
    Split text into sections by detecting heading lines.
    Each detected heading starts a new section; everything before the first
    heading is treated as a preamble section.
    """
    lines = text.splitlines()
    sections: list[str] = []
    current: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Page markers are structural — skip for section detection but keep in text
        if _PAGE_MARKER_RE.match(stripped):
            current.append(line)
            continue

        is_heading = (
            bool(_HEADING_RE.match(stripped))
            and 4 <= len(stripped) <= 120
        )
        if is_heading and current:
            section_text = "\n".join(current).strip()
            if section_text:
                sections.append(section_text)
            current = [line]
        else:
            current.append(line)

    if current:
        section_text = "\n".join(current).strip()
        if section_text:
            sections.append(section_text)

    return sections if sections else [text]


# ── Public API ────────────────────────────────────────────────────────────────

def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 100,
) -> list[str]:
    """
    Compatibility shim — returns a flat list of chunk strings.
    Does NOT include page metadata. New code should call prepare_chunks().
    """
    sections = _split_into_sections(text)
    chunks: list[str] = []
    for section in sections:
        chunks.extend(_split_text_block(section, chunk_size, overlap))
    return chunks


def prepare_chunks(
    text: str,
    source_id: str,
    title: str = "",
    category: str = "general",
    chunk_size: int = 800,
    overlap: int = 100,
) -> list[dict]:
    """
    Split document text into chunks ready for embedding + upsert.

    Each returned dict:
    {
        "text":        str,   # chunk content (body text or Markdown table)
        "source_id":   str,
        "title":       str,
        "category":    str,
        "chunk_index": int,   # sequential across whole document
        "page_start":  int,   # first page this chunk covers (1-indexed)
        "page_end":    int,   # last page (same as page_start for most chunks)
        "is_table":    bool,  # True if chunk is a Markdown table block
    }

    Pipeline:
      1. Build page index from '--- PAGE N ---' markers in text
      2. Split into sections by heading detection
      3. Within each section, handle table blocks as atomic units
      4. Split remaining body text with sentence-boundary awareness + overlap
      5. Tag each chunk with page numbers
      6. Drop chunks below MIN_CHUNK_CHARS
    """
    page_index = _build_page_index(text)
    sections = _split_into_sections(text)

    raw_chunks: list[dict] = []
    char_offset = 0     # approximate — used for page lookup

    for section in sections:
        lines = section.splitlines()
        table_blocks = _extract_table_blocks(lines)
        table_line_ranges = {(s, e) for s, e, _ in table_blocks}

        # ── Emit table blocks as atomic chunks ────────────────────────────
        for start_line, end_line, table_text in table_blocks:
            # Estimate character offset by scanning to this line
            approx_offset = char_offset + len("\n".join(lines[:start_line]))
            page = _offset_to_page(page_index, approx_offset)
            raw_chunks.append({
                "text": table_text,
                "page_start": page,
                "page_end": page,
                "is_table": True,
            })

        # ── Collect non-table text lines for body splitting ───────────────
        body_lines: list[str] = []
        for i, line in enumerate(lines):
            in_table = any(s <= i < e for s, e in table_line_ranges)
            if not in_table:
                body_lines.append(line)

        body_text = "\n".join(body_lines).strip()
        if body_text:
            sub_chunks = _split_text_block(body_text, chunk_size, overlap)
            for sc in sub_chunks:
                # Approximate page by finding first page marker reference in chunk
                page_marker = _PAGE_MARKER_RE.search(sc)
                if page_marker:
                    page = int(page_marker.group(1))
                else:
                    page = _offset_to_page(page_index, char_offset)
                raw_chunks.append({
                    "text": sc,
                    "page_start": page,
                    "page_end": page,
                    "is_table": False,
                })

        char_offset += len(section) + 1

    # ── Drop sub-threshold chunks, assign sequential index ────────────────────
    result: list[dict] = []
    pending = ""
    pending_meta: dict = {}

    for chunk_dict in raw_chunks:
        chunk_text_val = chunk_dict["text"]

        # Skip page markers in the text content
        chunk_text_val = _PAGE_MARKER_RE.sub("", chunk_text_val).strip()
        if not chunk_text_val:
            continue

        # Merge tiny non-table chunks into the previous one
        if not chunk_dict["is_table"] and len(chunk_text_val) < _MIN_CHUNK_CHARS and pending:
            pending += " " + chunk_text_val
            pending_meta["page_end"] = chunk_dict["page_end"]
            continue

        # Flush pending
        if pending:
            clean = _PAGE_MARKER_RE.sub("", pending).strip()
            if clean:
                result.append({
                    **pending_meta,
                    "text": clean,
                    "source_id": source_id,
                    "title": title,
                    "category": category,
                    "chunk_index": len(result),
                })
            pending = ""
            pending_meta = {}

        pending = chunk_text_val
        pending_meta = {
            "page_start": chunk_dict["page_start"],
            "page_end": chunk_dict["page_end"],
            "is_table": chunk_dict["is_table"],
        }

    # Flush final pending
    if pending:
        clean = _PAGE_MARKER_RE.sub("", pending).strip()
        if clean:
            result.append({
                **pending_meta,
                "text": clean,
                "source_id": source_id,
                "title": title,
                "category": category,
                "chunk_index": len(result),
            })

    return result
