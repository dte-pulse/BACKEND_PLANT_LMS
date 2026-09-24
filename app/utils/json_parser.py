"""Robust JSON parsing for LLM outputs.

Gemini sometimes returns JSON that is not strictly valid:

- conversational text around the JSON block
- markdown code fences
- trailing commas
- missing commas between keys / array elements
- truncated output (a string cut mid-way, or a missing closing bracket)
- single-quoted Python-style literals

``parse_json_robustly`` tries these strategies in order of increasing
forgiveness. As a last resort it salvages a truncated JSON *array* by
extracting every complete ``{...}`` object it can find (string-aware brace
matching), so a cut-off 5-question batch still yields the questions Gemini
finished instead of falling back to generic MCQs.
"""
import ast
import json
import logging
import re

logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────


def _extract_json_block(text: str) -> str:
    """Strip conversational/markdown text around the JSON payload.

    Returns the substring between the first ``[``/``{`` and the last
    ``]``/``}``, or the stripped text when no bracket pair is found.
    """
    raw = text.strip()
    start = -1
    for i, ch in enumerate(raw):
        if ch in '[{':
            start = i
            break
    end = -1
    for i in range(len(raw) - 1, -1, -1):
        if raw[i] in ']}':
            end = i
            break
    if start != -1 and end != -1 and end > start:
        return raw[start:end + 1]
    return raw


def _fix_trailing_commas(text: str) -> str:
    """Remove commas directly before ``}``/``]`` (``[1, 2,]`` -> ``[1, 2]``)."""
    return re.sub(r',\s*([}\]])', r'\1', text)


def _fix_missing_commas(text: str) -> str:
    """Insert commas a model forgot between structural tokens, e.g.
    ``} "key"`` -> ``}, "key"``, ``} {`` -> ``}, {``, or
    ``"value" "key"`` -> ``"value", "key"``.

    String-aware: never edits inside quoted values, and only inserts after a
    closing ``}``/``]``/string when the next non-whitespace character starts
    a new key or element (``"``, ``{`` or ``[``) — a position where valid
    JSON always has a comma, so valid input is never corrupted.
    """
    out = []
    in_dq = False  # inside "..."
    in_sq = False  # inside '...' (Python-style literals / prose guard)
    escaped = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_dq or in_sq:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif in_dq and ch == '"':
                in_dq = False
                # Closing quote of a string VALUE: if the next token starts a
                # new key or element, the model forgot the comma, e.g.
                # `"question": "Q?" "options": ...` (the 'Expecting ,
                # delimiter' ingestion failure).
                j = i + 1
                while j < n and text[j].isspace():
                    j += 1
                if j < n and text[j] in '"{[':
                    out.append(',')
            elif in_sq and ch == "'":
                in_sq = False
            i += 1
            continue
        if ch == '"':
            in_dq = True
            out.append(ch)
            i += 1
            continue
        if ch == "'":
            in_sq = True
            out.append(ch)
            i += 1
            continue
        if ch in '}]':
            out.append(ch)
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] in '"{[':
                out.append(',')
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _extract_json_objects(text: str) -> list[str]:
    """Return every complete ``{...}`` object in ``text`` as a raw string,
    using string-aware brace matching.

    Objects that are never closed (truncated output) are skipped — this is
    what lets a cut-off JSON array still yield the objects the model
    finished.
    """
    objects = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] != '{':
            i += 1
        if i >= n:
            break
        start = i
        depth = 0
        in_string = False
        escaped = False
        while i < n:
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == '\\':
                    escaped = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        objects.append(text[start:i + 1])
                        i += 1
                        break
            i += 1
    return objects


def _parse_loose(text: str):
    """Parse a JSON-ish snippet applying the trailing/missing comma repairs."""
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return json.loads(_fix_trailing_commas(text))
    except Exception:
        pass
    try:
        return json.loads(_fix_missing_commas(text))
    except Exception:
        pass
    try:
        val = ast.literal_eval(text)
        if isinstance(val, (dict, list)):
            return val
    except Exception:
        pass
    return None


def _recover_truncated_array(text: str):
    """Salvage a truncated JSON array: parse every complete object in it.

    Returns a list of dicts (possibly empty) when the payload clearly starts
    an array, else None.
    """
    stripped = text.lstrip()
    if not stripped.startswith('['):
        return None
    parsed = []
    for obj in _extract_json_objects(text):
        val = _parse_loose(obj)
        if isinstance(val, dict):
            parsed.append(val)
    return parsed if parsed else None


# ── public API ───────────────────────────────────────────────────────────────


def parse_json_robustly(text: str):
    """Robustly parse a JSON string, handling typical LLM formatting issues:
    - Conversational text before/after the JSON block
    - Markdown code blocks (```json ... ```)
    - Trailing commas in lists or objects
    - Missing commas between keys / array elements
    - Truncated arrays (recovered object-by-object)
    - Single quotes instead of double quotes (using ast.literal_eval)
    """
    if not text or not text.strip():
        raise ValueError('Empty JSON text')

    cleaned = _extract_json_block(text)

    # 1. First attempt: plain json.loads.
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. Fix trailing commas in objects and arrays.
    try:
        return json.loads(_fix_trailing_commas(cleaned))
    except Exception:
        pass

    # 3. Fix missing commas between keys / array elements (after trailing).
    try:
        return json.loads(_fix_missing_commas(_fix_trailing_commas(cleaned)))
    except Exception:
        pass

    # 4. Parse Python literal representation (e.g. single quotes, True/False/None).
    try:
        val = ast.literal_eval(cleaned)
        if isinstance(val, (dict, list)):
            return val
    except Exception as e:
        logger.warning("ast.literal_eval fallback failed: %s", e)

    # 5. Truncated array recovery — keep the objects the model finished.
    recovered = _recover_truncated_array(cleaned)
    if recovered is not None:
        return recovered

    # 6. Last resort: raise the original/detailed error.
    return json.loads(cleaned)
