"""
P2 #2 — section_index-tagged concept tree tests.

Trees generated at ingestion now carry explicit ``section_indexes`` per chapter
(direct references into the parent-chunk order), so the mind map groups by
index binding instead of fuzzy-matching LLM titles against chunk titles.
"""
from types import SimpleNamespace

import pytest


def _node(i, title, status='in_progress', score=0.0):
    return {
        'id': f'parent_{i}', 'type': 'parent', 'title': title,
        'status': status, 'knowledge_score': score,
        'children_total': 1, 'children_completed': 0,
        'children': [], 'summary': '', 'page_start': 1, 'page_end': 1,
    }


def _service(db=None, user_id=7):
    from app.services.mind_map_service import MindMapService
    return MindMapService(db, user_id)


class TestGroupBySectionIndexes:
    def test_groups_by_explicit_indexes(self):
        svc = _service()
        nodes = [_node(i, f'0.{i + 1} Topic') for i in range(4)]
        tree = [
            {'title': 'Foundations', 'section_indexes': [0, 1], 'children': []},
            {'title': 'Operations', 'section_indexes': [2, 3], 'children': []},
        ]
        grouped = svc._group_by_section_indexes(tree, nodes)
        assert grouped is not None
        assert [ch['title'] for ch in grouped] == ['Foundations', 'Operations']
        assert [s['title'] for s in grouped[0]['children']] == ['0.1 Topic', '0.2 Topic']
        assert [s['flat_parent_idx'] for s in grouped[0]['children']] == [0, 1]
        assert [s['flat_parent_idx'] for s in grouped[1]['children']] == [2, 3]

    def test_document_titles_win_over_llm_titles(self):
        svc = _service()
        nodes = [_node(0, 'CIP-Enabled Cleaning Cycle'), _node(1, 'Rinseability Verification')]
        tree = [
            {'title': 'Cleaning Regime', 'section_indexes': [0, 1], 'children': []},
        ]
        grouped = svc._group_by_section_indexes(tree, nodes)
        # The LLM chapter title stays for the chapter, but sections keep the
        # document's own titles (no override with LLM sub-topic titles).
        assert grouped[0]['title'] == 'Cleaning Regime'
        assert [s['title'] for s in grouped[0]['children']] == [
            'CIP-Enabled Cleaning Cycle', 'Rinseability Verification']

    def test_out_of_range_and_duplicate_indexes_ignored(self):
        svc = _service()
        nodes = [_node(0, 'A'), _node(1, 'B'), _node(2, 'C')]
        tree = [
            {'title': 'Ch1', 'section_indexes': [0, 99, -1, 1], 'children': []},
            {'title': 'Ch2', 'section_indexes': [1, 2], 'children': []},  # 1 already used → only 2
        ]
        grouped = svc._group_by_section_indexes(empty_safe(tree), nodes)
        assert grouped is not None
        ch1, ch2 = grouped[0], grouped[1]
        assert [s['flat_parent_idx'] for s in ch1['children']] == [0, 1]
        assert [s['flat_parent_idx'] for s in ch2['children']] == [2]

    def test_no_indexes_returns_none_legacy_fallback(self):
        svc = _service()
        nodes = [_node(0, 'A'), _node(1, 'B')]
        legacy_tree = [{'title': 'Ch', 'children': [{'title': 'A', 'children': []}]}]
        assert svc._group_by_section_indexes(legacy_tree, nodes) is None

    def test_low_coverage_returns_none(self):
        svc = _service()
        nodes = [_node(i, f'T{i}') for i in range(5)]
        tree = [{'title': 'Tiny', 'section_indexes': [0], 'children': []}]
        # 1/5 = 20% coverage < 70% → None
        assert svc._group_by_section_indexes(tree, nodes) is None

    def test_unmapped_nodes_land_in_appendix(self):
        svc = _service()
        nodes = [_node(i, f'T{i}') for i in range(4)]
        tree = [{'title': 'All', 'section_indexes': [0, 1, 2], 'children': []}]
        grouped = svc._group_by_section_indexes(tree, nodes)
        assert grouped is not None
        # 3/4 = 75% ≥ 70% coverage → indexed grouping succeeds
        assert grouped[-1]['title'] == 'Supporting Material'
        assert grouped[-1]['children'][0]['flat_parent_idx'] == 3

    def test_chapter_status_and_score_rollup(self):
        svc = _service()
        nodes = [
            _node(0, 'A', status='completed', score=90.0),
            _node(1, 'B', status='in_progress', score=50.0),
        ]
        tree = [{'title': 'Ch', 'section_indexes': [0, 1], 'children': []}]
        grouped = svc._group_by_section_indexes(tree, nodes)
        ch = grouped[0]
        assert ch['status'] == 'in_progress'
        assert ch['knowledge_score'] == 70.0
        assert ch['children_completed'] == 1

    def test_legacy_nested_section_index_schema_supported(self):
        svc = _service()
        nodes = [_node(0, 'A'), _node(1, 'B'), _node(2, 'C')]
        tree = [
            {'title': 'Ch1', 'children': [
                {'title': 'a', 'section_index': 0, 'children': []},
                {'title': 'x', 'children': []},           # no index → ignored
                {'title': 'b', 'section_index': 2, 'children': []},
            ]},
        ]
        grouped = svc._group_by_section_indexes(tree, nodes)
        # 2/3 = 66% coverage < 70% → falls back (by design: too thin to trust)
        assert grouped is None


def empty_safe(tree):
    """Guard helper: trees must be lists of dicts for the grouping path."""
    return [ch for ch in tree if isinstance(ch, dict)]
