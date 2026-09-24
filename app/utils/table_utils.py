"""
Table utilities (P2 #3 — make SOP tables retrievable).

Raw markdown/HTML table rows embed poorly (pipes and cell fragments carry no
sentence semantics) and BM25 tokenizes them into noise, so table chunks —
where pharmaceutical SOPs put their acceptance criteria, limits and specs —
were effectively invisible to retrieval.

Fix at ingestion:
1. ``is_table_chunk``     — detect markdown ``|…|`` tables and HTML ``<table>``
                            rows (mammoth emits HTML tables for DOCX).
2. ``serialize_table``    — turn a table into retrieval-friendly text:
                            a header line + ``Header: value`` pair per row, so
                            "hold time: 72 hours" exists as a phrase to embed
                            and to BM25-tokenize.
3. ``build_table_embed_text`` — the exact string fed to the embedding model
                            (caption, if any, first — then serialized rows).
4. ``build_table_lexical_text`` — the string appended to the BM25 corpus text
                            for the chunk (header words + cell values repeated
                            per row so per-row queries hit each row).

Deterministic and side-effect-free — heavily unit-tested; the LLM caption is
applied upstream (``LLMClient.generate_table_caption``).
"""

from __future__ import annotations

import html
import re
from typing import Optional

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

# A markdown table row: leading pipe, 1+ cells, trailing pipe (whitespace tolerant).
_MD_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")

# Header/body separator row of a markdown table: | --- | :---: | ...
_MD_SEPARATOR_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$")

_HTML_ROW_RE = re.compile(r"<tr\b", re.IGNORECASE)


def is_table_chunk(text: str) -> bool:
    """
    True when the chunk is predominantly a table.

    Markdown: needs >=2 ``|…|`` rows, of which >=1 is a non-separator row
    (a lone separator row is not a table). HTML: any ``<tr`` occurrence makes
    the chunk a table chunk (mammoth emits one <p> per table and row-level
    chunks are produced by the splitter, so even a single-row chunk counts).
    """
    if not text or not text.strip():
        return False

    if _HTML_ROW_RE.search(text):
        return True

    rows = [line for line in text.splitlines() if _MD_ROW_RE.match(line)]
    if len(rows) < 2:
        return False
    real_rows = [r for r in rows if not _MD_SEPARATOR_RE.match(r)]
    return len(real_rows) >= 1


def fraction_table_rows(text: str) -> float:
    """Fraction of non-empty lines that look like table rows (for diagnostics)."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return 0.0
    return sum(1 for ln in lines if _MD_ROW_RE.match(ln)) / len(lines)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _clean_cell(cell: str) -> str:
    """Normalize a table cell to plain single-spaced text."""
    cell = html.unescape(cell)
    cell = re.sub(r"<[^>]+>", " ", cell)     # strip inline tags (b, i, br, ...)
    cell = cell.replace("|", " ").replace(" ", " ")
    cell = re.sub(r"\s+", " ", cell).strip()
    return cell


def parse_markdown_table(text: str) -> list[list[str]]:
    """
    Parse a markdown table into a list of rows of cells.

    Header row and separator row are preserved positionally: the first
    non-empty row is the header, the separator (if present) is skipped.
    Returns [] when no markdown table is present.
    """
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not _MD_ROW_RE.match(line):
            continue
        if _MD_SEPARATOR_RE.match(line):
            continue
        # Strip the outer pipes, split on inner pipes.
        body = line.strip()
        if body.startswith("|"):
            body = body[1:]
        if body.endswith("|"):
            body = body[:-1]
        cells = [_clean_cell(c) for c in body.split("|")]
        rows.append(cells)
    return rows


_HTML_TAGS_SPLIT_RE = re.compile(r"</tr\s*>", re.IGNORECASE)
_CELL_TAG_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", re.IGNORECASE | re.DOTALL)


def parse_html_table(text: str) -> list[list[str]]:
    """
    Parse the first ``<table>`` found in ``text`` into rows of cells.

    Cell-level tags are cleaned by ``_clean_cell`` (mammoth output is plain
    but may carry <strong>, <em>, entities). Returns [] when no table exists.
    """
    m = re.search(r"<table\b.*?</table\s*>", text, re.IGNORECASE | re.DOTALL)
    # Tolerant fallback: a chunk split mid-table can carry bare <tr> rows
    # without the <table> wrapper (mammoth emits row-per-paragraph HTML).
    table_html = m.group(0) if m else (text if _HTML_ROW_RE.search(text) else None)
    if not table_html:
        return []
    rows: list[list[str]] = []
    # NOTE: no [1:] skip — the first split segment holds the FIRST row's cells
    # (<table><tr><th>…</th>) alongside the opening tags. Cell extraction is
    # tag-scoped (<td>/<th>), so opening-tag noise is harmless.
    for row_html in _HTML_TAGS_SPLIT_RE.split(table_html):
        cells = [
            _clean_cell(content)
            for content in _CELL_TAG_RE.findall(row_html)
        ]
        if cells:
            rows.append(cells)
    return rows


def parse_table(text: str) -> list[list[str]]:
    """Parse whichever table flavor the chunk contains. Empty list if none."""
    if _HTML_ROW_RE.search(text):
        return parse_html_table(text)
    return parse_markdown_table(text)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def serialize_table(rows: list[list[str]], max_rows: int = 100) -> str:
    """
    Render a parsed table as retrieval-friendly prose:

        Columns: Hold Time | Acceptance
        Hold Time: 72 hours | Acceptance: >= 6 logs
        Hold Time: 48 hours | Acceptance: >= 5 logs

    - Rows with fewer cells than the header are padded with "".
    - Empty header cells become "Column 2", "Column 3", ...
    - Empty-valued pairs are skipped (no "Hold Time: " noise).
    - Capped at ``max_rows`` data rows to bound prompt/embedding size.
    """
    if not rows:
        return ""

    header = rows[0]
    headers: list[str] = []
    for idx, cell in enumerate(header):
        headers.append(cell if cell else f"Column {idx + 1}")

    lines = ["Columns: " + " | ".join(headers)]
    for row in rows[1:max_rows + 1]:
        pairs: list[str] = []
        for idx in range(len(headers)):
            value = row[idx].strip() if idx < len(row) else ""
            if value:
                pairs.append(f"{headers[idx]}: {value}")
        if pairs:
            lines.append(" | ".join(pairs))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Retrieval text builders
# ---------------------------------------------------------------------------

def build_table_embed_text(
    serialized: str,
    caption: Optional[str] = None,
) -> str:
    """
    Text handed to the embedding model for a table chunk.

    The caption (LLM one-liner, e.g. "Temperature limits by storage zone")
    leads because it carries the sentence semantics the raw cells lack.
    """
    parts = []
    if caption:
        parts.append(caption.strip())
    if serialized:
        parts.append(serialized)
    return "\n".join(parts)


def build_table_lexical_text(
    serialized: str,
    caption: Optional[str] = None,
) -> str:
    """
    Text appended to the BM25 corpus entry for a table chunk.

    BM25 is bag-of-words with length normalization: header words appearing
    once in a 20-row table get diluted to irrelevance. Repeating the caption
    plus header line per data row restores their weight so a query like
    "hold time acceptance" scores every row-bearing chunk properly.
    """
    lines = [ln for ln in (serialized or "").splitlines() if ln.strip()]
    if not lines:
        return caption.strip() if caption else ""

    header_line = lines[0]
    data_lines = lines[1:]
    header_words = " ".join(
        w for w in header_line.replace("Columns:", " ").split() if w != "|"
    )

    parts: list[str] = []
    if caption:
        parts.append(caption.strip())
        parts.append(caption.strip())  # caption boost: most query-aligned text
    for line in data_lines:
        parts.append(header_words)
        parts.append(line)
    return "\n".join(parts)
