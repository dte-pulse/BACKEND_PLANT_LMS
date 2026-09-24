"""P2 #3 — Table Retrieval tests.

Covers:
- table detection (markdown + mammoth HTML), negative cases
- markdown/HTML parsing incl. separator rows, entities, inline tags
- serialization: padding, empty headers, empty values, row cap
- embed-text builder (caption leading) and lexical builder (header boost)
- ingestion wiring: table chunks embed caption+serialized rows and store the
  caption in contextual_header (drives the REAL _process_document Phase 2)
- BM25 corpus boost: table chunk text transforms, cache contract unchanged
"""
import pytest

from app.utils.table_utils import (
    build_table_embed_text,
    build_table_lexical_text,
    fraction_table_rows,
    is_table_chunk,
    parse_html_table,
    parse_markdown_table,
    parse_table,
    serialize_table,
)


MD_TABLE = """| Hold Time | Acceptance |
| --- | :---: |
| 72 hours | >= 6 logs |
| 48 hours | >= 5 logs |
"""


class TestDetection:
    def test_markdown_table_detected(self):
        assert is_table_chunk(MD_TABLE) is True

    def test_separator_only_is_not_table(self):
        assert is_table_chunk("| --- | --- |") is False

    def test_single_row_is_not_table(self):
        assert is_table_chunk("| a | b |") is False

    def test_prose_is_not_table(self):
        assert is_table_chunk("Hold time is 72 hours per SOP-001 section 5.2.") is False

    def test_empty_is_not_table(self):
        assert is_table_chunk("") is False
        assert is_table_chunk(None) is False

    def test_html_table_detected(self):
        assert is_table_chunk("<table><tr><td>a</td></tr></table>") is True

    def test_fraction_table_rows(self):
        # Header + separator + 2 data rows all match the row regex.
        assert fraction_table_rows(MD_TABLE) == 1.0
        assert fraction_table_rows("plain text") == 0.0


class TestParsing:
    def test_markdown_parse_skips_separator(self):
        rows = parse_markdown_table(MD_TABLE)
        assert rows == [
            ["Hold Time", "Acceptance"],
            ["72 hours", ">= 6 logs"],
            ["48 hours", ">= 5 logs"],
        ]

    def test_markdown_parse_ignores_prose_lines(self):
        rows = parse_markdown_table("intro text\n" + MD_TABLE + "outro")
        assert len(rows) == 3

    def test_html_parse_unescapes_and_strips_tags(self):
        html = (
            "<table><tr><th>Param</th><th>Limit</th></tr>"
            "<tr><td>pH&nbsp;range</td><td><strong>6.0</strong>&ndash;7.5</td></tr></table>"
        )
        rows = parse_html_table(html)
        assert rows == [["Param", "Limit"], ["pH range", "6.0 –7.5"]]

    def test_parse_table_routes_html(self):
        assert parse_table("<tr><td>x</td></tr>") == [["x"]]

    def test_parse_table_empty_for_prose(self):
        assert parse_table("no tables here") == []


class TestSerialization:
    def test_basic_pairs(self):
        out = serialize_table([["Hold Time", "Acceptance"], ["72 hours", ">= 6 logs"]])
        lines = out.splitlines()
        assert lines[0] == "Columns: Hold Time | Acceptance"
        assert lines[1] == "Hold Time: 72 hours | Acceptance: >= 6 logs"

    def test_short_row_padded(self):
        out = serialize_table([["A", "B", "C"], ["x"]])
        assert "A: x" in out
        assert "B:" not in out  # empty values skipped, not "B: "

    def test_empty_header_cells_named(self):
        out = serialize_table([["", "Limit"], ["v1", "10"]])
        assert out.splitlines()[0] == "Columns: Column 1 | Limit"

    def test_row_cap(self):
        rows = [["H"]] + [["v"]] * 150
        out = serialize_table(rows, max_rows=100)
        assert len(out.splitlines()) == 101  # header + 100 data rows

    def test_empty(self):
        assert serialize_table([]) == ""


class TestRetrievalTextBuilders:
    def test_embed_text_caption_leads(self):
        out = build_table_embed_text("Columns: A\nA: 1", caption="Table of limits")
        assert out.startswith("Table of limits\n")

    def test_embed_text_without_caption(self):
        assert build_table_embed_text("Columns: A\nA: 1") == "Columns: A\nA: 1"

    def test_lexical_text_repeats_headers_per_row(self):
        out = build_table_lexical_text(
            "Columns: Hold Time | Acceptance\nHold Time: 72 | Acceptance: 6"
        )
        assert out.splitlines() == [
            "Hold Time Acceptance",
            "Hold Time: 72 | Acceptance: 6",
        ]

    def test_lexical_text_caption_boosted(self):
        out = build_table_lexical_text(
            "Columns: A\nA: 1", caption="Table of acceptance limits"
        )
        lines = out.splitlines()
        assert lines[0] == "Table of acceptance limits"
        assert lines[1] == "Table of acceptance limits"

    def test_lexical_text_empty_serialized(self):
        assert build_table_lexical_text("", caption="cap") == "cap"
        assert build_table_lexical_text("", None) == ""


# ── Ingestion wiring ────────────────────────────────────────────────────────


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        r = self._result
        if isinstance(r, list):
            return r[0] if r else None
        return r

    def all(self):
        return self._result if isinstance(self._result, list) else [self._result]

    def delete(self):
        pass

    def update(self, *a, **k):
        pass


class _FakeDB:
    """DB double: all queries return empty lists (no previous-version data)."""

    def query(self, model):
        return _FakeQuery([])

    def add(self, obj):
        pass

    def add_all(self, objs):
        pass

    def flush(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass

    def refresh(self, obj):
        obj.id = getattr(obj, 'id', 1)


class TestIngestionWiring:
    """Drive _process_document with fakes so the REAL Phase-2 table wiring runs
    end-to-end: caption generation, embed-text re-pointing, header storage."""

    MD_CONTENT = MD_TABLE
    PROSE_CONTENT = "Hold time is 72 hours per SOP-001 section 5.2."

    def _make_svc(self, content, monkeypatch):
        from app.services import ingestion_service as ing

        test = self
        test.captions = []
        test.persisted_children = []

        class FakeLLM:
            def generate_contextual_header(self, *a, **k):
                return 'This chunk is from the hold time section.'

            def generate_table_caption(self, serialized, doc_title, section_title, user_id=0):
                test.captions.append(serialized)
                return f'Table of {doc_title} limits'

            def generate_topic_summary(self, *a, **k):
                return 'Summary'

            def generate_learning_card(self, *a, **k):
                return 'card'

            def compare_document_versions(self, *a, **k):
                return 'no changes'

            def generate_mind_map_structure(self, *a, **k):
                return []

        class FakeExtraction:
            def extract(self, *a, **k):
                return {'pages': {1: content}, 'toc': []}

        class FakeChunking:
            def split_pages(self, *a, **k):
                return {'parents': [{
                    'stable_id': 'SOP-T__hold', 'title': 'Hold Time',
                    'content': content, 'content_hash': 'h1', 'section_index': 0,
                    'chapter_num': None,
                    'page_start': 1, 'page_end': 1, 'token_count': 50,
                    'children': [{
                        'stable_id': 'SOP-T__hold__c_00', 'child_index': 0,
                        'chunk_index': 0, 'page_no': 1, 'token_count': 50,
                        'content': content, 'content_hash': 'h1',
                    }],
                }]}

        class FakeDocRepo:
            def get_by_id(self, _id):
                # Attribute-bag Document (ORM model __init__ would fight us).
                doc = type('Doc', (), {})()
                doc.id = 1
                doc.code = 'SOP-T'
                doc.title = 'Hold Time Study'
                doc.version = 1
                doc.topic_id = 1
                doc.subject_id = 1
                doc.status = 'processing'
                doc.file_type = 'pdf'
                doc.file_url = 's3://x'
                doc.file_name = 'x.pdf'
                return doc

            def update(self, d, **k):
                pass

        svc = ing.IngestionService.__new__(ing.IngestionService)
        svc.db = _FakeDB()
        svc.embedding_client = type('E', (), {
            # use_real=False → Phase 2 stamps get_hash_version(), no API needed.
            'use_real': False,
            'embed_text': staticmethod(lambda t: [0.0] * 8),
        })()
        svc.llm_client = FakeLLM()
        svc.extraction_service = FakeExtraction()
        svc.chunking_service = FakeChunking()
        svc.document_repository = FakeDocRepo()
        svc.mcq_repository = type('M', (), {
            'create_many': lambda self, rows: None})()

        def _capture_add(obj):
            if type(obj).__name__ == 'Chunk':
                test.persisted_children.append(obj)
            obj.id = getattr(obj, 'id', 1)

        svc.db.add = _capture_add

        # No Gemini key → deterministic MCQ fallbacks (no LLM calls).
        # settings is imported inside _generate_section_mcqs — patch at source.
        monkeypatch.setattr('app.core.config.settings.gemini_api_key', '',
                            raising=False)
        monkeypatch.setattr(ing, 'FileStorageService',
                            lambda: type('S', (), {'use_s3': False})(),
                            raising=False)
        monkeypatch.setattr(ing, 'SemanticCacheService',
                            lambda: type('C', (), {
                                'invalidate_document': lambda s, d: None})(),
                            raising=False)
        return svc

    def _run(self, svc):
        from app.services.ingestion_service import IngestionService

        doc = svc.document_repository.get_by_id(1)
        return IngestionService._process_document(svc, doc)

    def test_table_chunk_captions_and_embed_text(self, monkeypatch):
        """Table chunk: caption generated over serialized rows, caption stored
        in contextual_header, pipeline completes end-to-end."""
        svc = self._make_svc(self.MD_CONTENT, monkeypatch)
        result = self._run(svc)

        assert self.captions, 'caption should have been generated for the table chunk'
        assert 'Columns: Hold Time | Acceptance' in self.captions[0]

        child = self.persisted_children[0]
        # Caption stored in contextual_header (Upgrade-2 channel).
        assert child.contextual_header == 'Table of Hold Time Study limits'
        assert result['status'] == 'ready'
        assert result['chunk_count'] == 1

    def test_prose_chunk_unaffected(self, monkeypatch):
        """Prose chunks keep the generated contextual header — no caption,
        no serialization, pipeline completes."""
        svc = self._make_svc(self.PROSE_CONTENT, monkeypatch)
        result = self._run(svc)

        assert self.captions == []
        child = self.persisted_children[0]
        assert child.contextual_header == 'This chunk is from the hold time section.'
        assert result['status'] == 'ready'


# ── BM25 corpus boost ───────────────────────────────────────────────────────


class _FakeChunk:
    def __init__(self, content, header=None):
        self.content = content
        self.contextual_header = header


class TestBM25ChunkText:
    def test_table_chunk_gets_serialized_boost(self):
        from app.services.rag_service import _bm25_chunk_text

        chunk = _FakeChunk(MD_TABLE, header='Table of hold time limits')
        text = _bm25_chunk_text(chunk)
        assert 'Hold Time: 72 hours' in text
        assert 'Hold Time Acceptance' in text                # per-row header boost
        assert text.count('Table of hold time limits') == 2  # caption boost
        # No raw pipe-rows remain (pair separators "A: x | B: y" are fine —
        # it's leading/trailing cell-delimiter pipes that tokenize as noise).
        assert not any(ln.lstrip().startswith('|') for ln in text.splitlines())

    def test_prose_chunk_unchanged(self):
        from app.services.rag_service import _bm25_chunk_text

        prose = 'Hold time is 72 hours.'
        assert _bm25_chunk_text(_FakeChunk(prose)) == prose

    def test_unparseable_table_falls_back_to_content(self, monkeypatch):
        from app.services import rag_service as rs
        import app.utils.table_utils as tu

        chunk = _FakeChunk(MD_TABLE)
        monkeypatch.setattr(
            tu, 'serialize_table',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')),
            raising=False)
        assert rs._bm25_chunk_text(chunk) == MD_TABLE

    def test_bm25_cache_contract_unchanged(self):
        """Corpus text transform must not change the (doc, version) cache."""
        from app.services import rag_service as rs

        rs._BM25_CACHE.clear()
        chunks = [_FakeChunk(MD_TABLE), _FakeChunk('prose chunk here')]
        i1 = rs._get_bm25_index(42, 3, chunks)
        i2 = rs._get_bm25_index(42, 3, chunks)
        assert i1 is i2
