"""
Structure-type classification (P2 #6).

One label per document, computed ONCE at ingest from the same chunk_result
that produced the sections, stored on ``documents.structure_type`` and read
at query time by the retrieval threshold logic (rag_service):

    structured    — real heading hierarchy survived extraction (PDF TOC /
                    bookmarks, numbered X.Y headings, markdown headings, or
                    DOCX heading styles via the P2 #4 fallback). Section
                    boundaries are trustworthy → strict thresholds.
    unstructured  — blind token batching had to invent the sections (LLM or
                    first-sentence titles, P2 #5 clustering). Boundaries are
                    approximate → relaxed thresholds.
    (None/legacy) — 'unknown' → structured defaults (pre-#6 behavior).

Deterministic, cheap, no LLM calls.
"""
from __future__ import annotations

import re

# Marker keys chunking_service sets ONLY on heading-derived parents. A blind
# batch never carries them: chapter metadata comes from the numbered-heading
# detector, and cluster parents are labeled 'cluster'.
_STRUCTURE_MARKERS = ('chapter_num', 'heading_level')


def _looks_numbered(title: str) -> bool:
    """True for titles like '1. Introduction', '2.3 Architecture'."""
    return bool(re.match(r'^\d{1,2}(\.\d{1,2}){0,2}\.?\s+\S', (title or '').strip()))


def classify_structure_type(parents: list[dict]) -> str:
    """Classify from the ingest chunk_result's parent list.

    The pipeline is a cascade: TOC → numbered headings → markdown headings →
    clustering/blind batching. Whichever branch produced the parents is what
    defines the document's structure quality, so classification reads the
    OUTPUT rather than re-parsing the raw text.
    """
    from app.utils.constants import (
        STRUCTURE_STRUCTURED,
        STRUCTURE_UNSTRUCTURED,
    )

    if not parents:
        return STRUCTURE_UNSTRUCTURED

    n = len(parents)
    n_cluster = sum(
        1 for p in parents if (p.get('title') or '').strip().lower().startswith('cluster')
    )
    if n_cluster / n > 0.5:
        return STRUCTURE_UNSTRUCTURED

    n_marked = sum(
        1 for p in parents
        if any(p.get(k) is not None for k in _STRUCTURE_MARKERS)
    )
    n_numbered = sum(1 for p in parents if _looks_numbered(p.get('title')))
    # A clear majority of sections carry real structure → structured.
    if (n_marked + n_numbered) / n >= 0.7:
        return STRUCTURE_STRUCTURED

    return STRUCTURE_UNSTRUCTURED
