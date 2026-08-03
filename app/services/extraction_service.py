import re
import logging
import tempfile
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)


# ── Code keywords that strongly suggest a line is code ────────────────────────
_CODE_START_PATTERNS = re.compile(
    r'^('
    r'public\s|private\s|protected\s|static\s|class\s|interface\s|enum\s|'
    r'import\s|package\s|void\s|int\s|float\s|double\s|boolean\s|char\s|'
    r'long\s|short\s|byte\s|String\s|var\s|final\s|abstract\s|'
    r'return\s|return;|throw\s|throws\s|'
    r'if\s*\(|else\s*\{|else\s+if|for\s*\(|while\s*\(|do\s*\{|'
    r'switch\s*\(|case\s|break;|continue;|'
    r'try\s*\{|catch\s*\(|finally\s*\{|'
    r'@Override|@\w+|'
    r'def\s|elif\s|print\s*\(|lambda\s|yield\s|'
    r'\/\/|\/\*|\*\s'
    r')'
)

_CODE_INLINE_PATTERNS = re.compile(
    r'[{};]|'                          # braces / semicolons
    r'\w+\s*\([^)]*\)\s*[{;]|'        # method/constructor calls ending ; or {
    r'\w+\s+\w+\s*=\s*new\s+\w+|'     # Type var = new Type
    r'=>\s*\{|'                         # lambda arrow
    r'\w+\.\w+\('                       # chained method call
)


def _code_score(line: str) -> int:
    """Return a numeric 'code likelihood' score for a single line."""
    s = line.strip()
    if not s:
        return 0

    score = 0

    if _CODE_START_PATTERNS.match(s):
        score += 4

    inline_matches = len(_CODE_INLINE_PATTERNS.findall(s))
    score += inline_matches * 2

    score += s.count(';')
    score += s.count('{') + s.count('}')

    # Penalise long prose-looking lines with no code symbols
    if len(s.split()) > 8 and not any(c in s for c in (';', '{', '}', '()')):
        score -= 3

    return score


def _enhance_markdown(text: str) -> str:
    """
    Post-process extracted markdown to:
    1. Detect runs of code-like lines and wrap them in fenced code blocks.
    2. Normalise paragraph spacing so every paragraph is separated by \\n\\n.

    Works for both mammoth (docx) and pymupdf4llm (pdf) output.
    """
    lines = text.split('\n')
    result: list[str] = []
    i = 0
    in_fence = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Track existing fenced blocks — don't touch them
        if stripped.startswith('```'):
            in_fence = not in_fence
            result.append(line)
            i += 1
            continue

        if in_fence:
            result.append(line)
            i += 1
            continue

        score = _code_score(line)

        if score >= 4:
            # Collect the full run of code-like lines
            code_block: list[str] = [line]
            j = i + 1
            while j < len(lines):
                next_score = _code_score(lines[j])
                next_stripped = lines[j].strip()
                # Include blank lines inside a run if surrounded by code
                if next_score >= 2:
                    code_block.append(lines[j])
                    j += 1
                elif not next_stripped and j + 1 < len(lines) and _code_score(lines[j + 1]) >= 2:
                    code_block.append(lines[j])
                    j += 1
                else:
                    break

            # Drop trailing blank lines inside the collected block
            while code_block and not code_block[-1].strip():
                code_block.pop()

            if len(code_block) >= 1:
                result.append('')
                result.append('```java')
                # Write code lines as-is (no double-spacing inside code)
                result.extend(code_block)
                result.append('```')
                result.append('')
                i = j
                continue

        result.append(line)
        i += 1

    joined = '\n'.join(result)

    # Normalise paragraph spacing in prose only (outside fences):
    # Split on fences, process odd segments (prose), leave even segments (code) alone.
    parts = re.split(r'(```.*?```)', joined, flags=re.DOTALL)
    normalised_parts = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            # Inside a fence — keep as-is
            normalised_parts.append(part)
        else:
            # Prose — normalise newlines
            p = re.sub(r'\n{3,}', '\n\n', part)
            p = re.sub(r'([^\n])\n([^\n])', r'\1\n\n\2', p)
            normalised_parts.append(p)

    return ''.join(normalised_parts).strip()


class ExtractionService:
    def extract(self, file_path: str, file_type: str) -> dict:
        local_path = file_path
        temp_file = None

        # If it's a remote URL, check local cache or download it
        if file_path.startswith('http://') or file_path.startswith('https://'):
            from app.core.config import settings
            from urllib.parse import urlparse
            parsed = urlparse(file_path)
            filename = Path(parsed.path).name
            local_fallback = Path(settings.upload_dir) / filename
            if local_fallback.exists() and local_fallback.is_file():
                logger.info(f"Using local cached copy at {local_fallback} instead of downloading {file_path}")
                local_path = str(local_fallback)
            else:
                try:
                    logger.info(f"Downloading remote document from {file_path} for extraction...")
                    suffix = f".{file_type}"
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                    with httpx.Client() as client:
                        response = client.get(file_path)
                        response.raise_for_status()
                        temp_file.write(response.content)
                    temp_file.close()
                    local_path = temp_file.name
                except Exception as e:
                    logger.error(f"Failed to download remote file {file_path}: {e}")
                    if temp_file:
                        try:
                            Path(temp_file.name).unlink(missing_ok=True)
                        except Exception:
                            pass
                    raise ValueError(f"Could not download remote file: {e}")

        try:
            if file_type == 'pdf':
                return self._extract_pdf(local_path)
            if file_type == 'docx':
                return {'pages': self._extract_docx(local_path), 'toc': []}
            raise ValueError('Unsupported file type')
        finally:
            if temp_file:
                try:
                    Path(temp_file.name).unlink(missing_ok=True)
                except Exception as e:
                    logger.error(f"Failed to delete temp file {temp_file.name}: {e}")

    def _extract_pdf(self, file_path: str) -> dict:
        try:
            import fitz
            doc = fitz.open(file_path)
            toc = doc.get_toc()
            doc.close()
        except Exception as e:
            logger.warning(f"Failed to read PDF TOC outline: {e}")
            toc = []

        try:
            import pymupdf4llm
            md_pages = pymupdf4llm.to_markdown(file_path, page_chunks=True)
            pages = []
            for idx, page in enumerate(md_pages, start=1):
                text = page.get('text', '').strip()
                if text:
                    enhanced = _enhance_markdown(text)
                    pages.append({'page_no': idx, 'text': enhanced, 'format': 'markdown'})
            return {'pages': pages, 'toc': toc}
        except Exception as e:
            logger.warning(f"pymupdf4llm unavailable ({e}), falling back to standard fitz extraction")
            import fitz
            doc = fitz.open(file_path)
            pages = []
            for idx, page in enumerate(doc, start=1):
                text = page.get_text().strip()
                if text:
                    enhanced = _enhance_markdown(text)
                    pages.append({'page_no': idx, 'text': enhanced, 'format': 'plain'})
            doc.close()
            return {'pages': pages, 'toc': toc}

    def _extract_docx(self, file_path: str) -> list[dict]:
        try:
            import mammoth
            with open(file_path, 'rb') as f:
                result = mammoth.convert_to_markdown(f)
            text = result.value.strip()
        except Exception:
            import docx
            doc = docx.Document(file_path)
            text = '\n'.join([p.text for p in doc.paragraphs if p.text.strip()])

        if not text:
            return []

        enhanced = _enhance_markdown(text)
        logger.info(f"Docx extraction enhanced: {len(text)} → {len(enhanced)} chars")
        return [{'page_no': 1, 'text': enhanced, 'format': 'markdown'}]
