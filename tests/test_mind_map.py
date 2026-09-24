"""
Mind map service tests (P2 #13).

The chapter grouping, version diffing and title-cleaning logic in
mind_map_service previously had ZERO coverage — and it is exactly the part
users complain about ("mind map doesn't match the document"). These tests
lock down:

- _clean_node_title artifact stripping (recaps, page cites, markdown)
- _extract_chapter_number / chapter grouping for numbered documents (M-6)
- deterministic-tree preference over the LLM tree (M-6/M-7)
- version diffing: new / modified / unchanged / removed nodes
- synthetic module grouping for parent-less documents
- tree_regenerate_mode + build_regenerated_tree (deterministic paths)
"""
from collections import defaultdict
from types import SimpleNamespace

import pytest


# ── Fakes ─────────────────────────────────────────────────────────────────────

def _parent(pid, title, section_index=1, content_hash='h1', stable_id=None,
            summary='', page_start=1, page_end=2):
    return SimpleNamespace(
        id=pid, title=title, section_index=section_index,
        content_hash=content_hash, stable_id=stable_id or f'ps_{pid}',
        summary=summary, page_start=page_start, page_end=page_end,
        content=f'{title} — section body content for the outline builder.',
    )


def _child(cid, parent_id, content, content_hash='h1', stable_id=None,
           child_index=1, page_no=1):
    return SimpleNamespace(
        id=cid, parent_chunk_id=parent_id, content=content,
        content_hash=content_hash, stable_id=stable_id or f'cs_{cid}',
        child_index=child_index, page_no=page_no,
    )


def _doc(doc_id=1, code='DOC-1', title='Test Document', mind_map_json=None, version=1):
    return SimpleNamespace(
        id=doc_id, code=code, title=title, mind_map_json=mind_map_json, version=version,
    )


def _model_key(model):
    name = getattr(model, '__name__', None)
    if name:
        return {
            'Document': 'doc',
            'ParentChunk': 'parent',
            'Chunk': 'chunk',
            'ParentChunkProgress': 'progress',
            'ChildChunkAttempt': 'attempts',
        }.get(name, 'other')
    # e.g. ParentChunk.title (InstrumentedAttribute)
    if getattr(model, 'key', None) == 'title':
        return 'ptitle'
    return 'other'


class FakeQuery:
    def __init__(self, db, model):
        self._db = db
        self._model = model

    def options(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def all(self):
        return self._db._rows(self._model)

    def first(self):
        return self._db._first(self._model)


class FakeDB:
    """Routes queries by model + call order (matches build_mind_map's order:
    doc lookup → parents → children → progress → attempts → prev doc →
    prev parents → prev children)."""

    def __init__(self, document, parents=(), children=(), prev_doc=None,
                 prev_parents=(), prev_children=(), parent_title_rows=None):
        self.document = document
        self.parents = list(parents)
        self.children = list(children)
        self.prev_doc = prev_doc
        self.prev_parents = list(prev_parents)
        self.prev_children = list(prev_children)
        self.parent_title_rows = parent_title_rows or []
        self.counts = defaultdict(int)

    def query(self, model, *a, **k):
        self.counts[_model_key(model)] += 1
        return FakeQuery(self, model)

    def _rows(self, model):
        key = _model_key(model)
        if key == 'doc':
            return []
        if key == 'parent':
            # 1st query = current parents, 2nd = prev-version parents
            return self.parents if self.counts['parent'] == 1 else self.prev_parents
        if key == 'chunk':
            return self.children if self.counts['chunk'] == 1 else self.prev_children
        if key == 'ptitle':
            return self.parent_title_rows
        return []  # progress / attempts / anything else

    def _first(self, model):
        key = _model_key(model)
        if key == 'doc':
            # 1st Document query = the document, 2nd = prev-version lookup
            return self.document if self.counts['doc'] == 1 else self.prev_doc
        rows = self._rows(model)
        return rows[0] if rows else None


# ── Title cleaning ────────────────────────────────────────────────────────────

class TestCleanNodeTitle:
    def test_strips_recap_prefix_and_page_cites(self):
        from app.services.mind_map_service import _clean_node_title
        raw = "[Preceding Section: Cleaning Validation]\n... Rinse with WFI.\n|12|Cleaning of Tanks"
        assert _clean_node_title(raw) == 'Cleaning of Tanks'

    def test_strips_markdown_and_truncates(self):
        from app.services.mind_map_service import _clean_node_title
        raw = '**' + 'x' * 80 + '**'
        out = _clean_node_title(raw)
        assert len(out) <= 60 and out.endswith('...')

    def test_fallback_for_empty(self):
        from app.services.mind_map_service import _clean_node_title
        # Empty input short-circuits to '' — the 'Section Topic' fallback only
        # applies when there IS text but no meaningful line survives filtering.
        assert _clean_node_title('') == ''
        assert _clean_node_title(None) == ''
        # Text with no meaningful line (e.g. only page numbers) → fallback.
        assert _clean_node_title('123\n456') == 'Section Topic'

    def test_skips_url_and_table_lines(self):
        from app.services.mind_map_service import _clean_node_title
        # Table separator lines (2+ of | - +) and URLs must be skipped.
        raw = "https://example.com/docs\n+----+----+\nReal Section Heading Here"
        assert _clean_node_title(raw) == 'Real Section Heading Here'


class TestChapterNumber:
    def test_extracts_top_level_number(self):
        from app.services.mind_map_service import _extract_chapter_number
        assert _extract_chapter_number('1.1 Purpose') == '1'
        assert _extract_chapter_number('2.3 Architecture') == '2'

    def test_unnumbered_maps_to_zero(self):
        from app.services.mind_map_service import _extract_chapter_number
        assert _extract_chapter_number('Table of Contents') == '0'


# ── Synthetic grouping (parent-less documents) ────────────────────────────────

class TestSyntheticGrouping:
    def test_flat_chunks_grouped_into_modules_of_five(self):
        from app.services.mind_map_service import MindMapService
        doc = _doc(mind_map_json=None)
        chunks = [_child(i, None, f'Topic {i} content here', stable_id=None) for i in range(1, 8)]
        svc = MindMapService(FakeDB(doc, children=chunks), user_id=7)
        data = svc.build_mind_map(1)

        assert data['total_parents'] == 2  # ceil(7/5)
        assert len(data['nodes']) == 2
        assert data['nodes'][0]['children_total'] == 5
        assert data['nodes'][1]['children_total'] == 2
        assert data['nodes'][0]['title'].startswith('Module 1:')
        assert data['nodes'][0]['children'][0]['id'] == 'child_1'

    def test_no_chunks_returns_message(self):
        from app.services.mind_map_service import MindMapService
        svc = MindMapService(FakeDB(_doc()), user_id=7)
        data = svc.build_mind_map(1)
        assert data['nodes'] == []
        assert 'No chunks' in data['message']


# ── Chapter grouping for numbered documents ──────────────────────────────────

NUMBERED_PARENTS = [
    _parent(1, '1.1 Purpose'),
    _parent(2, '1.2 Scope'),
    _parent(3, '2.1 Safety'),
]
NUMBERED_CHILDREN = [
    _child(11, 1, 'Purpose of this procedure is defined here.'),
    _child(12, 2, 'Scope covers the production area only.'),
    _child(13, 3, 'Safety precautions must be followed.'),
]


class TestChapterGrouping:
    def _build(self, mind_map_json=None):
        from app.services.mind_map_service import MindMapService
        doc = _doc(mind_map_json=mind_map_json)
        svc = MindMapService(FakeDB(doc, parents=NUMBERED_PARENTS, children=NUMBERED_CHILDREN), user_id=7)
        return svc.build_mind_map(1)

    def test_numbered_docs_group_by_chapter_number(self):
        data = self._build()
        chapters = data['nodes']
        assert [c['title'] for c in chapters] == ['1. Purpose', '2. Safety']
        assert chapters[0]['children_total'] == 2
        assert chapters[1]['children_total'] == 1

    def test_flat_parent_idx_injected_for_frontend_nav(self):
        data = self._build()
        sections = [s for ch in data['nodes'] for s in ch['children']]
        assert sorted(s['flat_parent_idx'] for s in sections) == [0, 1, 2]

    def test_lock_sequencing_first_parent_unlocked(self):
        data = self._build()
        flat = data['flat_nodes']
        assert flat[0]['status'] == 'in_progress'  # first section is unlocked
        assert all(n['status'] == 'locked' for n in flat[1:])

    def test_llm_tree_bypassed_for_numbered_docs(self):
        # M-6: an LLM-invented semantic tree must NOT drive grouping when the
        # document carries its own numbered structure.
        llm_tree = [{'title': 'Invented Chapter', 'children': [{'title': 'Unrelated Topic', 'children': []}]}]
        data = self._build(mind_map_json=llm_tree)
        assert [c['title'] for c in data['nodes']] == ['1. Purpose', '2. Safety']

    def test_deterministic_tree_still_used_for_numbered_docs(self):
        # M-7: a DETERMINISTIC tree (numbered sub-topics) IS document-accurate
        # and must keep driving the grouping.
        det_tree = [{'title': '1. Introduction', 'children': [
            {'title': '1.1 Purpose', 'children': []},
            {'title': '1.2 Scope', 'children': []},
        ]}]
        data = self._build(mind_map_json=det_tree)
        assert data['nodes'][0]['title'] == '1. Introduction'
        assert data['nodes'][0]['children'][0]['title'] == '1.1 Purpose'


# ── Version diffing ───────────────────────────────────────────────────────────

class TestVersionDiffing:
    def _fixture(self):
        current_parents = [
            _parent(1, '1.1 Purpose', content_hash='new', stable_id='ps_1'),
            _parent(2, '1.2 Scope', content_hash='same', stable_id='ps_2'),
        ]
        current_children = [
            _child(11, 1, 'Purpose content.', content_hash='same', stable_id='cs_1'),
            _child(12, 2, 'Scope content.', content_hash='same', stable_id='cs_2'),
        ]
        prev_doc = _doc(doc_id=99, code='DOC-1', version=0)
        prev_parents = [
            _parent(101, '1.1 Purpose', content_hash='old', stable_id='ps_1'),
            _parent(102, '1.2 Scope', content_hash='same', stable_id='ps_2'),
            _parent(103, '1.3 Old Section', content_hash='old', stable_id='ps_3'),
        ]
        prev_children = [
            _child(201, 101, 'Purpose content.', content_hash='same', stable_id='cs_1'),
            _child(202, 102, 'Scope content.', content_hash='same', stable_id='cs_2'),
            _child(203, 103, 'Old content.', content_hash='old', stable_id='cs_3'),
        ]
        doc = _doc(mind_map_json=None)
        db = FakeDB(doc, parents=current_parents, children=current_children,
                    prev_doc=prev_doc, prev_parents=prev_parents, prev_children=prev_children)
        return db, doc

    def test_modified_unchanged_and_new_statuses(self):
        from app.services.mind_map_service import MindMapService
        db, _ = self._fixture()
        data = MindMapService(db, user_id=7).build_mind_map(1)
        by_stable = {n['stable_id']: n for n in data['flat_nodes']}
        assert by_stable['ps_1']['version_status'] == 'modified'   # hash changed
        assert by_stable['ps_2']['version_status'] == 'unchanged'  # hash same

    def test_removed_parents_appended_with_removed_status(self):
        from app.services.mind_map_service import MindMapService
        db, _ = self._fixture()
        data = MindMapService(db, user_id=7).build_mind_map(1)
        removed = [n for n in data['flat_nodes'] if n['version_status'] == 'removed']
        assert len(removed) == 1
        assert removed[0]['stable_id'] == 'ps_3'
        assert removed[0]['title'].startswith('[Removed]')
        assert removed[0]['status'] == 'locked'
        assert removed[0]['children'][0]['title'].startswith('[Removed]')

    def test_child_diff_status_reflects_hash(self):
        from app.services.mind_map_service import MindMapService
        db, _ = self._fixture()
        data = MindMapService(db, user_id=7).build_mind_map(1)
        # children are NESTED inside flat_nodes parents, not top-level entries
        nested = [c for p in data['flat_nodes'] for c in p['children']]
        child = [c for c in nested if c['stable_id'] == 'cs_1'][0]
        assert child['version_status'] == 'unchanged'


# ── Regeneration helpers (P1 #2 extraction) ───────────────────────────────────

class TestRegenerateHelpers:
    def test_mode_numbered_is_deterministic(self):
        from app.services.mind_map_service import tree_regenerate_mode
        db = FakeDB(_doc(), parent_title_rows=[('1.1 Purpose',), ('1.2 Scope',)])
        assert tree_regenerate_mode(db, _doc()) == 'deterministic'

    def test_mode_unnumbered_needs_llm(self):
        from app.services.mind_map_service import tree_regenerate_mode
        db = FakeDB(_doc(), parent_title_rows=[('Introduction',), ('Overview',)])
        assert tree_regenerate_mode(db, _doc()) == 'llm'

    def test_recovery_rebuilds_chapters_from_stale_llm_tree(self):
        from app.services.mind_map_service import build_regenerated_tree
        parents = [_parent(1, '1.1 Purpose'), _parent(2, '1.2 Scope'), _parent(3, '2.1 Safety')]
        doc = _doc(mind_map_json=[{'title': 'Semantic', 'children': [{'title': 'Vibes', 'children': []}]}])
        db = FakeDB(doc, parents=parents)
        tree, used_llm = build_regenerated_tree(db, doc, user_id=7)
        assert used_llm is False
        assert [ch['title'] for ch in tree] == ['Chapter 1', 'Chapter 2']
        assert tree[0]['children'] == [{'title': '1.1 Purpose', 'children': []},
                                       {'title': '1.2 Scope', 'children': []}]

    def test_stored_deterministic_tree_preserved(self):
        from app.services.mind_map_service import build_regenerated_tree
        parents = [_parent(1, '1.1 Purpose')]
        stored = [{'title': '1. Intro', 'children': [{'title': '1.1 Purpose', 'children': []}]}]
        doc = _doc(mind_map_json=stored)
        db = FakeDB(doc, parents=parents)
        tree, used_llm = build_regenerated_tree(db, doc, user_id=7)
        assert tree is stored and used_llm is False
