"""
Small-to-big retrieval tests (parent-context answer synthesis).

Chunk-level retrieval stays authoritative for scoring/citations; generate_answer
now rebuilds the LLM context at SECTION granularity. These tests lock down:
- _build_parent_sections: grouping, ordering, limits, synthetic parents
- _fetch_section_context: parent fetch, sibling hydration, missing-parent fallback
- _render_section_context: SECTION headers, (ctx) tagging, overflow index, budget
"""
from types import SimpleNamespace

import pytest


def _child(cid, parent_id, child_index, content, page_no=1, chunk_index=None):
    return SimpleNamespace(
        id=cid,
        parent_chunk_id=parent_id,
        child_index=child_index,
        content=content,
        page_no=page_no,
        chunk_index=chunk_index if chunk_index is not None else cid,
        contextual_header=None,
        document_id=1,
    )


def _parent(pid, title='Section', section_index=1, summary='', page_start=1, page_end=2):
    return SimpleNamespace(
        id=pid, title=title, section_index=section_index, summary=summary,
        page_start=page_start, page_end=page_end,
    )


class TestBuildParentSections:
    def test_groups_children_by_parent_preserving_first_seen_order(self):
        from app.services.rag_service import RagService

        # Retrieval order: child of parent 2 first → section 2 must come first
        chunks = [
            _child(21, 2, 1, 'b content'),
            _child(11, 1, 1, 'a content'),
            _child(22, 2, 2, 'b2 content'),
        ]
        sections = RagService._build_parent_sections(chunks)
        assert [s['parent_id'] for s in sections] == [2, 1]
        assert sections[0]['retrieved_children'][0].id == 21
        assert sections[0]['page_start'] == sections[0]['page_end']

    def test_top_sections_limit(self):
        from app.services.rag_service import RagService
        chunks = [_child(i, i, 1, f'content {i}') for i in range(1, 6)]
        sections = RagService._build_parent_sections(chunks, top_sections=2)
        assert len(sections) == 2

    def test_synthetic_parent_none_id(self):
        from app.services.rag_service import RagService
        chunks = [_child(1, None, 1, 'orphan content')]
        sections = RagService._build_parent_sections(chunks)
        assert len(sections) == 1
        assert sections[0]['has_parent_row'] is False


class TestFetchSectionContext:
    def test_hydrates_parent_and_siblings(self):
        from app.services.rag_service import RagService
        from app.models.parent_chunk import ParentChunk
        from app.models.chunk import Chunk

        parent = _parent(2, title='2.3 Architecture', summary='System layout',
                         page_start=4, page_end=6)
        retrieved = _child(21, 2, 2, 'middle child', page_no=5)
        sibling1 = _child(20, 2, 1, 'first child', page_no=4)
        sibling3 = _child(22, 2, 3, 'last child', page_no=6)

        class FakeQuery:
            def __init__(self, rows):
                self._rows = rows

            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return self._rows

        class FakeDB:
            def query(self, model):
                if model is ParentChunk:
                    return FakeQuery([parent])
                if model is Chunk:
                    # sibling query is per-section; return all siblings for parent 2
                    return FakeQuery([sibling1, retrieved, sibling3])
                return FakeQuery([])

        svc = RagService.__new__(RagService)
        svc.db = FakeDB()

        sections = [{
            'parent_id': 2, 'title': '', 'summary': '', 'page_start': 5,
            'page_end': 5, 'retrieved_children': [retrieved],
            'has_parent_row': True, 'all_children': [], 'children_total': 1,
        }]
        svc._fetch_section_context(sections)

        s = sections[0]
        assert s['title'] == '2.3 Architecture'
        assert s['summary'] == 'System layout'
        assert s['page_start'] == 4 and s['page_end'] == 6
        assert [c.id for c in s['all_children']] == [20, 21, 22]  # ordered by child_index
        assert s['children_total'] == 3

    def test_missing_parent_row_falls_back_to_retrieved_only(self):
        from app.services.rag_service import RagService
        from app.models.parent_chunk import ParentChunk
        from app.models.chunk import Chunk

        class FakeQuery:
            def __init__(self, rows):
                self._rows = rows

            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return self._rows

        class FakeDB:
            def query(self, model):
                return FakeQuery([])  # nothing found

        svc = RagService.__new__(RagService)
        svc.db = FakeDB()
        retrieved = _child(21, 2, 2, 'content')
        sections = [{
            'parent_id': 2, 'title': '', 'summary': '', 'page_start': 5,
            'page_end': 5, 'retrieved_children': [retrieved],
            'has_parent_row': True, 'all_children': [], 'children_total': 1,
        }]
        svc._fetch_section_context(sections)
        assert sections[0]['has_parent_row'] is False


class TestRenderSectionContext:
    def _section(self, all_children, retrieved, title='2.3 Architecture',
                 page_start=4, page_end=6):
        return {
            'parent_id': 2, 'title': title, 'summary': 'System layout',
            'page_start': page_start, 'page_end': page_end,
            'retrieved_children': retrieved, 'has_parent_row': True,
            'all_children': all_children, 'children_total': len(all_children),
        }

    def test_renders_section_header_and_all_siblings(self):
        from app.services.rag_service import RagService

        c1 = _child(20, 2, 1, 'first child text', page_no=4)
        c2 = _child(21, 2, 2, 'retrieved child text', page_no=5)
        out = RagService._render_section_context([self._section([c1, c2], [c2])])
        assert 'SECTION: 2.3 Architecture' in out
        assert 'System layout' in out
        assert 'Pages 4–6' in out
        assert '[Page 4, Chunk 20]' in out  # non-retrieved sibling present
        assert '[Page 5, Chunk 21]' in out
        assert 'first child text' in out

    def test_context_only_siblings_are_tagged(self):
        from app.services.rag_service import RagService

        c1 = _child(20, 2, 1, 'context sibling', page_no=4)
        c2 = _child(21, 2, 2, 'retrieved child', page_no=5)
        out = RagService._render_section_context([self._section([c1, c2], [c2])])
        # non-retrieved sibling tagged (ctx); retrieved one is not
        assert '[Page 4, Chunk 20] (ctx)' in out
        assert '[Page 5, Chunk 21] (ctx)' not in out

    def test_budget_trims_sections(self):
        from app.services.rag_service import RagService

        # ~450 realistic words per chunk (tiktoken compresses repetitive chars,
        # so use prose) → way past the 300-token budget after section 1.
        text1 = ('The validated cleaning procedure requires attention. ' * 40).strip()
        text2 = ('Media fill simulation must follow gowning protocol. ' * 40).strip()
        big1 = _child(1, 1, 1, text1, page_no=1)
        big2 = _child(2, 2, 1, text2, page_no=2)
        s1 = {
            'parent_id': 1, 'title': 'S1 Cleaning', 'summary': '', 'page_start': 1,
            'page_end': 1, 'retrieved_children': [big1], 'has_parent_row': True,
            'all_children': [big1], 'children_total': 1,
        }
        s2 = {
            'parent_id': 2, 'title': 'S2 Media Fill', 'summary': '', 'page_start': 2,
            'page_end': 2, 'retrieved_children': [big2], 'has_parent_row': True,
            'all_children': [big2], 'children_total': 1,
        }
        out = RagService._render_section_context([s1, s2], max_context_tokens=300)
        assert 'SECTION: S1' in out
        assert 'SECTION: S2' not in out  # second section exceeded the budget

    def test_synthetic_section_renders_retrieved_children(self):
        from app.services.rag_service import RagService

        c = _child(1, None, 1, 'orphan text', page_no=3)
        s = {
            'parent_id': None, 'title': '', 'summary': '', 'page_start': 3,
            'page_end': 3, 'retrieved_children': [c], 'has_parent_row': False,
            'all_children': [], 'children_total': 1,
        }
        out = RagService._render_section_context([s])
        assert '[Page 3, Chunk 1]' in out
        assert 'orphan text' in out
