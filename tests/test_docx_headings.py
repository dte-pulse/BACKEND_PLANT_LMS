"""P2 #4 — DOCX fallback heading preservation tests.

Covers:
- Word heading styles ("Heading 1"/"Heading 2") → markdown levels
- bold-only pseudo-headings → markdown headings (real docx: python-docx
  tolerates missing style parts, so Heading-style paragraphs render with
  empty style names and the bold detector is what recovers them)
- body paragraphs, punctuation-terminated bold lines, long bold lines stay body
- tables → HTML (feeds table_utils)
- page assembly still ~250 words/page and now split at headings
- extraction routes the mammoth failure path through the new fallback
"""
import pytest

from app.services.extraction_service import ExtractionService


def _para(text, style_name='Normal', bold=False):
    """Build a python-docx-like paragraph double."""

    class _Run:
        def __init__(self, text, bold):
            self.text = text
            self.bold = bold

    class _Para:
        def __init__(self):
            self.text = text
            self.runs = [_Run(text, bold)] if text else []
            self.style = type('S', (), {'name': style_name})()

    return _Para()


class _DocDouble:
    def __init__(self, paragraphs, tables=None):
        self.paragraphs = paragraphs
        self.tables = tables or []


class TestDocxFallback:
    def _write_docx(self, tmp_path, paragraphs, tables=None):
        """Real .docx (zip) is required — python-docx opens by filename."""
        import json
        import zipfile

        body = ''.join(
            f'<w:p><w:r><w:t>{p.text}</w:t></w:r></w:p>' for p in paragraphs
        )
        # Minimal but valid docx: python-docx only needs [Content_Types],
        # a rels entry pointing at document.xml, and the document part.
        document_xml = (
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body>{body}</w:body></w:document>'
        )
        content_types = (
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>'
        )
        rels = (
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        )
        path = tmp_path / 'doc.docx'
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('[Content_Types].xml', content_types)
            zf.writestr('_rels/.rels', rels)
            zf.writestr('word/document.xml', document_xml)
        return str(path)

    def test_style_headings_become_markdown(self, tmp_path, monkeypatch):
        svc = ExtractionService()
        paras = [
            _para('Standard Operating Procedure'),
            _para('Purpose', 'Heading 1'),
            _para('This SOP defines the hold time study.'),
            _para('Safety Precautions', 'Heading 2'),
            _para('Wear gloves at all times.'),
        ]
        monkeypatch.setattr(
            'docx.Document', lambda p: _DocDouble(paras), raising=False)
        # mammoth is present in the venv — force its conversion to fail so the
        # fallback path runs (this is the path under test).
        monkeypatch.setattr('mammoth.convert_to_markdown',
                            lambda f: (_ for _ in ()).throw(RuntimeError('no')))

        text = svc._extract_docx_fallback(self._write_docx(tmp_path, paras))
        assert '# Purpose' in text
        assert '## Safety Precautions' in text
        assert '# Standard Operating Procedure' not in text  # Normal style → body

    def test_bold_only_paragraphs_become_headings(self, tmp_path, monkeypatch):
        svc = ExtractionService()
        paras = [
            _para('Intro paragraph before any heading.'),
            _para('Safety Precautions', 'Normal', bold=True),
            _para('Wear gloves at all times.'),
            _para('Storage Conditions', 'Normal', bold=True),
            _para('Store between 2 and 8 degrees.'),
        ]
        monkeypatch.setattr(
            'docx.Document', lambda p: _DocDouble(paras), raising=False)
        monkeypatch.setattr('mammoth.convert_to_markdown',
                            lambda f: (_ for _ in ()).throw(RuntimeError('no')))

        text = svc._extract_docx_fallback(self._write_docx(tmp_path, paras))
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines[0] == 'Intro paragraph before any heading.'
        assert '### Safety Precautions' in text
        assert '### Storage Conditions' in text

    def test_bold_with_terminal_punctuation_stays_body(self, tmp_path, monkeypatch):
        svc = ExtractionService()
        paras = [
            _para('Wear gloves.', 'Normal', bold=True),   # sentence, not heading
            _para('Body text follows here.'),
        ]
        monkeypatch.setattr(
            'docx.Document', lambda p: _DocDouble(paras), raising=False)
        monkeypatch.setattr('mammoth.convert_to_markdown',
                            lambda f: (_ for _ in ()).throw(RuntimeError('no')))

        text = svc._extract_docx_fallback(self._write_docx(tmp_path, paras))
        assert '### ' not in text
        assert 'Wear gloves.' in text

    def test_long_bold_paragraph_stays_body(self, tmp_path, monkeypatch):
        svc = ExtractionService()
        long_bold = 'This bold run is far too long to be a heading ' * 3
        paras = [
            _para(long_bold, 'Normal', bold=True),
            _para('Body.'),
        ]
        monkeypatch.setattr(
            'docx.Document', lambda p: _DocDouble(paras), raising=False)
        monkeypatch.setattr('mammoth.convert_to_markdown',
                            lambda f: (_ for _ in ()).throw(RuntimeError('no')))

        text = svc._extract_docx_fallback(self._write_docx(tmp_path, paras))
        assert '### ' not in text

    def test_mixed_bold_runs_do_not_qualify(self, tmp_path, monkeypatch):
        """A paragraph with a non-bold run is emphasized body text, not a heading."""
        svc = ExtractionService()

        class _Run:
            def __init__(self, text, bold):
                self.text = text
                self.bold = bold

        para = type('P', (), {})()
        para.text = 'Bold start then plain tail'
        para.runs = [_Run('Bold start ', True), _Run('then plain tail', False)]
        para.style = type('S', (), {'name': 'Normal'})()

        paras = [para, _para('Body.')]
        monkeypatch.setattr(
            'docx.Document', lambda p: _DocDouble(paras), raising=False)
        monkeypatch.setattr('mammoth.convert_to_markdown',
                            lambda f: (_ for _ in ()).throw(RuntimeError('no')))

        text = svc._extract_docx_fallback(self._write_docx(tmp_path, paras))
        assert '### ' not in text

    def test_tables_serialized_as_html(self, tmp_path, monkeypatch):
        svc = ExtractionService()

        class _Cell:
            def __init__(self, text):
                self.text = text

        class _Row:
            def __init__(self, cells):
                self.cells = cells

        class _Table:
            def __init__(self, rows):
                self.rows = rows

        table = _Table([
            _Row([_Cell('Hold Time'), _Cell('Acceptance')]),
            _Row([_Cell('72 hours'), _Cell('6 logs')]),
        ])
        paras = [_para('See the table below.')]
        monkeypatch.setattr(
            'docx.Document',
            lambda p: _DocDouble(paras, tables=[table]), raising=False)
        monkeypatch.setattr('mammoth.convert_to_markdown',
                            lambda f: (_ for _ in ()).throw(RuntimeError('no')))

        text = svc._extract_docx_fallback(self._write_docx(tmp_path, paras))
        assert '<tr><td>Hold Time</td><td>Acceptance</td></tr>' in text
        # Round-trips through table_utils (P2 #3).
        from app.utils.table_utils import is_table_chunk, parse_table, serialize_table
        assert is_table_chunk(text)
        rows = parse_table(text)
        assert rows[0] == ['Hold Time', 'Acceptance']
        assert 'Hold Time: 72 hours' in serialize_table(rows)

    def test_empty_document_returns_empty_string(self, tmp_path, monkeypatch):
        svc = ExtractionService()
        monkeypatch.setattr(
            'docx.Document', lambda p: _DocDouble([]), raising=False)
        monkeypatch.setattr('mammoth.convert_to_markdown',
                            lambda f: (_ for _ in ()).throw(RuntimeError('no')))
        assert svc._extract_docx_fallback(
            self._write_docx(tmp_path, [])) == ''

    def test_corrupt_file_returns_empty_string(self, tmp_path):
        svc = ExtractionService()
        bad = tmp_path / 'bad.docx'
        bad.write_bytes(b'not a zip')
        assert svc._extract_docx_fallback(str(bad)) == ''

    def test_real_docx_end_to_end(self, tmp_path):
        """Real python-docx + mammoth integration: bold headings recovered,
        body preserved, no exception."""
        import zipfile

        svc = ExtractionService()
        document_xml = (
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Safety Precautions</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Always wear nitrile gloves.</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
        content_types = (
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>'
        )
        rels = (
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        )
        path = tmp_path / 'real.docx'
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('[Content_Types].xml', content_types)
            zf.writestr('_rels/.rels', rels)
            zf.writestr('word/document.xml', document_xml)

        pages = svc._extract_docx(str(path))
        full_text = '\n\n'.join(p['text'] for p in pages)
        assert 'Safety Precautions' in full_text
        assert 'nitrile gloves' in full_text


class TestPageAssembly:
    def test_pages_still_built_at_word_boundaries(self, tmp_path, monkeypatch):
        """~250 words/page assembly is preserved with heading paragraphs."""
        svc = ExtractionService()
        paras = (
            [_para('Doc Title', 'Heading 1')]
            + [_para(f'Sentence number {i} of the body text.') for i in range(120)]
        )
        monkeypatch.setattr(
            'docx.Document', lambda p: _DocDouble(paras), raising=False)
        monkeypatch.setattr('mammoth.convert_to_markdown',
                            lambda f: (_ for _ in ()).throw(RuntimeError('no')))

        path = TestDocxFallback()._write_docx(tmp_path, paras)
        pages = svc._extract_docx(path)
        assert len(pages) >= 2
        assert all(p['format'] == 'markdown' for p in pages)
        # ~1200 words → ~5 pages
        assert 3 <= len(pages) <= 8

    def test_extract_docx_pages_have_markdown_format(self, tmp_path, monkeypatch):
        svc = ExtractionService()
        paras = [_para('Only one short page here.')]
        monkeypatch.setattr(
            'docx.Document', lambda p: _DocDouble(paras), raising=False)
        monkeypatch.setattr('mammoth.convert_to_markdown',
                            lambda f: (_ for _ in ()).throw(RuntimeError('no')))
        path = TestDocxFallback()._write_docx(tmp_path, paras)
        pages = svc._extract_docx(path)
        assert pages == [{
            'page_no': 1,
            'text': 'Only one short page here.',
            'format': 'markdown',
        }]
