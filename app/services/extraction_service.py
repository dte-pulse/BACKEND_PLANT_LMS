import re
import logging
import tempfile
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)


# ── G-7: OCR helpers for image-only / scanned PDF pages ───────────────────────
def _ocr_page(file_path: str, page_index: int) -> str:
    """Attempt pytesseract OCR on a single PDF page (0-indexed).
    Returns extracted text, or empty string if OCR is unavailable."""
    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
        import io

        doc = fitz.open(file_path)
        page = doc.load_page(page_index)
        pix = page.get_pixmap(dpi=200)
        doc.close()
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img).strip()
        if text:
            logger.info(f"G-7 OCR recovered {len(text)} chars on page {page_index + 1}")
        return text
    except ImportError:
        logger.debug("pytesseract / PIL not installed \u2014 OCR fallback skipped.")
        return ""
    except Exception as ocr_err:
        logger.warning(f"G-7 OCR failed on page {page_index + 1}: {ocr_err}")
        return ""


def _ocr_page_fitz(page, page_no: int) -> str:
    """Attempt pytesseract OCR on an already-opened fitz page object."""
    try:
        import pytesseract
        from PIL import Image
        import io

        pix = page.get_pixmap(dpi=200)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        text = pytesseract.image_to_string(img).strip()
        if text:
            logger.info(f"G-7 OCR recovered {len(text)} chars on page {page_no}")
        return text
    except ImportError:
        return ""
    except Exception as ocr_err:
        logger.warning(f"G-7 OCR failed on page {page_no}: {ocr_err}")
        return ""


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


def _detect_code_language(code_lines: list) -> str:
    """G-17: Heuristic language detection for fenced code blocks.
    Falls back to empty string (generic fence) when no language can be determined."""
    combined = '\n'.join(code_lines).lower()
    if re.search(r'\bdef \b|\belif \b|import numpy|import pandas|#!/usr/bin/env python', combined):
        return 'python'
    if re.search(r'\bselect\b.+\bfrom\b|insert into |create table |drop table ', combined):
        return 'sql'
    if 'console.log' in combined or re.search(r'\bconst \b|\blet \b|\bvar \b', combined) or '=>' in combined:
        return 'javascript'
    if 'system.out' in combined or 'public class' in combined or 'import java.' in combined:
        return 'java'
    # Pharmaceutical / generic shell scripts
    if combined.lstrip().startswith('#!') or re.search(r'\becho \b|\bgrep \b|\bawk \b', combined):
        return 'bash'
    return ''  # generic — no language hint


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
                lang = _detect_code_language(code_block)  # G-17
                result.append('')
                result.append(f'```{lang}')
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
                # G-7: OCR fallback for image-only / scanned pages
                if not text:
                    text = _ocr_page(file_path, idx - 1)
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
                # G-7: OCR fallback for image-only / scanned pages
                if not text:
                    text = _ocr_page_fitz(page, idx)
                if text:
                    enhanced = _enhance_markdown(text)
                    pages.append({'page_no': idx, 'text': enhanced, 'format': 'plain'})
            doc.close()
            return {'pages': pages, 'toc': toc}

    def _extract_docx(self, file_path: str) -> list[dict]:
        # P2 #4 — DOCX fallback heading preservation. mammoth emits <h1>-<h6>
        # markdown for Word heading styles, so its normal path is already
        # structure-aware. The OLD fallback (python-docx paragraphs joined with
        # '\n') threw that structure away — every parent section became one
        # blind token-batch titled by its first sentence, corrupting the mind
        # map and degrading retrieval. The fallback now converts to markdown
        # itself (docx-level styles + bold-only run detection).
        try:
            import mammoth
            with open(file_path, 'rb') as f:
                result = mammoth.convert_to_markdown(f)
            text = result.value.strip()
            # mammoth escapes markdown punctuation in text runs ('1\.' for
            # '1.', 'service\.' for 'service.'). Downstream structure detection
            # is regex-based: the escaped '1\\.' broke the numbered-heading
            # detector, collapsing well-formed SOPs into one merged section,
            # misclassifying them 'unstructured' and destroying the
            # deterministic mind map. Unescape exactly the set mammoth escapes.
            text = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|>~])', r'\1', text)
        except Exception:
            text = self._extract_docx_fallback(file_path)

        if not text:
            return []

        enhanced = _enhance_markdown(text)
        logger.info(f"Docx extraction enhanced: {len(text)} \u2192 {len(enhanced)} chars")

        # G-1: Split DOCX into estimated pages at paragraph boundaries.
        # Assumes ~250 words per printed page (standard A4 single-spaced).
        # This gives learners meaningful page citations instead of always page 1.
        words_per_page = 250
        paragraphs = [p for p in enhanced.split('\n\n') if p.strip()]
        if not paragraphs:
            return [{'page_no': 1, 'text': enhanced, 'format': 'markdown'}]

        pages: list[dict] = []
        current_paras: list[str] = []
        current_words = 0
        page_no = 1

        for para in paragraphs:
            word_count = len(para.split())
            if current_words + word_count > words_per_page and current_paras:
                pages.append({
                    'page_no': page_no,
                    'text': '\n\n'.join(current_paras),
                    'format': 'markdown',
                })
                page_no += 1
                current_paras = [para]
                current_words = word_count
            else:
                current_paras.append(para)
                current_words += word_count

        if current_paras:
            pages.append({
                'page_no': page_no,
                'text': '\n\n'.join(current_paras),
                'format': 'markdown',
            })

        return pages or [{'page_no': 1, 'text': enhanced, 'format': 'markdown'}]

    @staticmethod
    def _extract_docx_fallback(file_path: str) -> str:
        """P2 #4 — python-docx fallback that PRESERVES heading structure.

        Word documents where mammoth fails still carry their structure in two
        places, both recovered here:

        1. Paragraph styles — "Heading 1"-"Heading 9" (and localized variants
           such as "berschrift 1" or "Titre 1") map to markdown levels.
           Style name matching is level-driven (an int suffix is authoritative;
           otherwise the embedded digit wins), so unknown style-name languages
           still resolve.
        2. Bold-only short paragraphs — Word authors mark pseudo-headings with
           Ctrl+B instead of a heading style ("Safety Precautions:"). A short
           paragraph whose runs are non-empty and predominantly bold becomes a
           markdown heading. Levels are assigned by a simple counter rather
           than guessed (font size is unreliable across templates).

        Output feeds the normal markdown pipeline (chunker's _split_by_headings,
        numbered-heading detector, mind map tree) — exactly what the mammoth
        path produces. Tables are emitted as HTML so table_utils can serialize
        them. Returns '' when the file yields nothing usable."""
        import re as _re

        try:
            import docx
            doc = docx.Document(file_path)
        except Exception as e:
            logger.warning(f"DOCX fallback extraction failed: {e}")
            return ''

        _HEADING_DIGIT = _re.compile(r'(\d)')

        def heading_level(style_name: str) -> int | None:
            """Markdown level for a paragraph style, or None when not a heading.

            Level logic: prefer the LAST int in the name — "Heading 1"→1 but a
            style literally named "Heading 10" must give 10, not 1. When no int
            exists but the name contains a known localized base word, treat it
            as a chapter-level heading (level 1)."""
            if not style_name:
                return None
            name = style_name.strip().lower()
            if not name:
                return None
            base_words = ('heading', 'berschrift', 'titre', 'título',
                          'titolo', '標題', 'заголовок', '見出し')
            ints = _HEADING_DIGIT.findall(name)
            if ints:
                return max(1, min(9, int(ints[-1])))
            if any(w in name for w in base_words):
                return 1
            return None

        def is_bold_heading_para(para) -> int | None:
            """Markdown level for a bold-only pseudo-heading, or None.

            Strict gates keep body text out: at least one non-empty run, ALL
            runs bold, ≤120 chars, and no terminal sentence punctuation."""
            text = (para.text or '').strip()
            if not text or len(text) > 120:
                return None
            runs = [r for r in para.runs if (r.text or '').strip()]
            if not runs:
                return None
            if not all(bool(r.bold) for r in runs):
                return None
            if text[-1:] in '.!?:;':
                return None
            return 3  # constant pseudo-level: bold headings form one tier

        out: list[str] = []
        bold_count = 0
        for para in doc.paragraphs:
            text = (para.text or '').strip()
            style = getattr(para.style, 'name', '') or ''
            level = heading_level(style)
            if level:
                if text:
                    out.append('')
                    out.append(f"{'#' * level} {text}")
                    out.append('')
                continue
            if not text:
                continue
            bold_level = is_bold_heading_para(para)
            if bold_level:
                bold_count += 1
                out.append('')
                out.append(f"{'#' * bold_level} {text}")
                out.append('')
                continue
            out.append(text)

        # Tables: emit as HTML so table_utils (P2 #3) can parse + serialize
        # them for retrieval. Skip inside the paragraph loop is fine — docx
        # tables are separate block containers, not paragraph children.
        for table in getattr(doc, 'tables', []) or []:
            try:
                rows_html = []
                for row in table.rows:
                    cells = ''
                    for c in row.cells:
                        cell_text = (c.text or '').strip()
                        cells += f'<td>{cell_text}</td>'
                    rows_html.append(f'<tr>{cells}</tr>')
                if rows_html:
                    out.append('')
                    out.append('<table>' + ''.join(rows_html) + '</table>')
                    out.append('')
            except Exception as te:
                logger.debug(f'DOCX fallback: table serialization skipped: {te}')

        text = '\n'.join(out).strip()
        if not text:
            return ''
        if bold_count:
            logger.info(
                f'DOCX fallback: recovered {bold_count} bold pseudo-heading(s)')
        return text
