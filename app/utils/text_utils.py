"""
Shared text utilities for the RAG pipeline.

Centralises two helpers that previously lived in multiple services:
- ``strip_preceding_context`` — removes the ``[Preceding Section: ...]`` recap
  that is prepended to parent/child chunk content during chunking. The recap is
  display/continuity metadata only; it must never be embedded, hashed, used in
  learning-card/MCQ generation, or sent to the LLM as retrieval context.
- ``representative_sample`` — returns a beginning + middle + end sample of a
  text so that no single section is silently truncated out of an LLM call.
"""
import re

import logging

logger = logging.getLogger(__name__)


# Matches the recap block injected by ChunkingService:
#   [Preceding Section: <title>]
#   ... <last paragraph of previous section>
#
_RECAP_BLOCK_RE = re.compile(
    r'^\[Preceding Section:[^\]]*\][ \t]*\n?'
    r'(?:\.\.\..*?(?:\n|$))?'
    r'\n?',
    re.MULTILINE,
)
# Defensive: strip a recap even if its continuation line is missing/malformed.
_RECAP_LINE_RE = re.compile(r'^\[Preceding Section:[^\]]*\][^\n]*\n?', re.MULTILINE)


def strip_preceding_context(text: str) -> str:
    """Remove the ``[Preceding Section: ...]`` recap injected for RAG continuity.

    Safe to call on any content — if there is no recap the input is returned
    unchanged (minus surrounding whitespace).
    """
    if not text:
        return ''
    cleaned = _RECAP_BLOCK_RE.sub('', text)
    cleaned = _RECAP_LINE_RE.sub('', cleaned)
    # Collapse the double blank line the recap removal can leave behind.
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def representative_sample(text: str, max_chars: int = 12_000) -> str:
    """Return a representative slice of ``text`` capped at ``max_chars``.

    Uses beginning + middle + end sampling so later sections of a long document
    are not silently excluded from embeddings, summaries or comparisons.
    """
    if not text:
        return ''
    if len(text) <= max_chars:
        return text

    portion = max_chars // 3
    mid = len(text) // 2
    return (
        text[:portion]
        + '\n\n[...middle section...]\n\n'
        + text[mid - portion // 2: mid + portion // 2]
        + '\n\n[...later section...]\n\n'
        + text[-portion:]
    )
