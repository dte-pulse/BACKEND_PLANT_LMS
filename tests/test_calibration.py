"""
Threshold-calibration sweep tests (P2 #9 'ongoing' + R-1).

The sweep must patch the #6 constants IN PLACE (the production gate path),
restore them on exit, exercise the per-type branch via stub docs carrying
structure_type, and produce sane recommendations. Synthetic-only — no DB/API.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_bm25_cache():
    """Synthetic eval runs warm the process-global BM25 index cache with stub
    corpora keyed (doc_id, version). Save/restore around every test here so
    nothing leaks into the other retrieval suites (their doc doubles share
    doc_id=1 with the synthetic corpus)."""
    from app.services import rag_service as rs

    saved = dict(rs._BM25_CACHE)
    yield
    rs._BM25_CACHE.clear()
    rs._BM25_CACHE.update(saved)


class TestThresholdPatching:
    def test_patcher_sets_and_restores(self):
        from app.utils import constants
        from app.evals.retrieval_eval import _PatchedThresholds

        orig_rel = dict(constants.RELEVANCE_THRESHOLD_BY_TYPE)
        orig_floor = dict(constants.MIN_COSINE_FLOOR_BY_TYPE)
        with _PatchedThresholds({'structured': (0.11, 0.02)}):
            assert constants.RELEVANCE_THRESHOLD_BY_TYPE['structured'] == 0.11
            assert constants.MIN_COSINE_FLOOR_BY_TYPE['structured'] == 0.02
            # Untouched type keeps its value
            assert constants.RELEVANCE_THRESHOLD_BY_TYPE['unstructured'] == orig_rel['unstructured']
        assert constants.RELEVANCE_THRESHOLD_BY_TYPE == orig_rel
        assert constants.MIN_COSINE_FLOOR_BY_TYPE == orig_floor

    def test_patcher_restores_on_exception(self):
        from app.utils import constants
        from app.evals.retrieval_eval import _PatchedThresholds

        orig_rel = dict(constants.RELEVANCE_THRESHOLD_BY_TYPE)
        with pytest.raises(RuntimeError):
            with _PatchedThresholds({'structured': (0.11, 0.02)}):
                raise RuntimeError('boom')
        assert constants.RELEVANCE_THRESHOLD_BY_TYPE == orig_rel

    def test_impl_reads_patched_constants(self):
        """_structure_thresholds must pick up in-place patches — that's the
        whole reason the sweep patches constants instead of rag globals."""
        from types import SimpleNamespace
        from app.services.rag_service import _structure_thresholds
        from app.evals.retrieval_eval import _PatchedThresholds

        doc = SimpleNamespace(structure_type='structured')
        with _PatchedThresholds({'structured': (0.11, 0.02)}):
            rel, floor = _structure_thresholds(doc)
        assert (rel, floor) == (0.11, 0.02)


class TestSyntheticCalibration:
    def test_run_synthetic_calibration(self):
        from app.evals.retrieval_eval import run_synthetic_calibration

        report = run_synthetic_calibration()
        assert report['mode'] == 'synthetic'
        # Both structure types present in the seed golden set must be swept.
        assert set(report['types'].keys()) >= {'structured', 'unstructured'}
        for s_type, r in report['types'].items():
            assert 0.10 <= r['best_relevance_threshold'] <= 0.60
            assert 0.10 <= r['best_cosine_floor'] <= r['best_relevance_threshold']
            assert r['accuracy'] == 1.0, f'{s_type}: {r["misses"]}'

    def test_grid_pairs_floor_le_rel(self):
        from app.evals.retrieval_eval import _grid_pairs, _CAL_GRID

        pairs = _grid_pairs()
        assert len(pairs) < len(_CAL_GRID) ** 2  # diagonal cut applied
        assert all(floor <= rel for rel, floor in pairs)
        assert (0.10, 0.10) in pairs  # most permissive corner present

    def test_sweep_restores_constants(self):
        """Running the sweep must leave the constants module untouched."""
        from app.utils import constants
        from app.evals.retrieval_eval import run_synthetic_calibration

        before_rel = dict(constants.RELEVANCE_THRESHOLD_BY_TYPE)
        before_floor = dict(constants.MIN_COSINE_FLOOR_BY_TYPE)
        run_synthetic_calibration()
        assert constants.RELEVANCE_THRESHOLD_BY_TYPE == before_rel
        assert constants.MIN_COSINE_FLOOR_BY_TYPE == before_floor

    def test_memoized_embedder(self):
        from app.evals.retrieval_eval import _MemoEmbedder

        calls = []

        class Inner:
            def embed_text(self, text):
                calls.append(text)
                return [0.0]

            def get_model_version(self):
                return 'inner-v1'

        m = _MemoEmbedder(Inner())
        m.embed_text('same question')
        m.embed_text('same question')
        assert calls == ['same question']
        assert m.get_model_version() == 'inner-v1'


class TestStubDocResolution:
    def test_stub_doc_query_honors_filters(self):
        """The _DocQuery double must apply equality filters so the impl
        resolves the right stub doc (structure_type) per document_id."""
        from app.evals.retrieval_eval import _StubDoc, _FakeDB
        from app.models.document import Document

        docs = [_StubDoc(1, 'SOP-A', structure_type='structured'),
                _StubDoc(2, 'SOP-B', structure_type='unstructured')]
        db = _FakeDB([], docs=docs)

        row = db.query(Document).filter(Document.id == 2).first()
        assert row is not None and row.structure_type == 'unstructured'
        assert db.query(Document).filter(Document.id == 99).first() is None

    def test_synth_sweep_env_resolves_docs(self):
        from app.evals.retrieval_eval import _synth_sweep_env
        from app.models.document import Document

        services = _synth_sweep_env()
        assert services
        for code, svc in services.items():
            doc_id = svc.db._docs and list(svc.db._docs)[0]
            row = svc.db.query(Document).filter(Document.id == doc_id).first()
            assert row is not None, f'no stub doc for {code}'
            assert row.structure_type in ('structured', 'unstructured')


class TestEntryTyping:
    def test_entry_structure_type_defaults(self):
        from app.evals.golden_qa import GoldenQA
        from app.evals.retrieval_eval import _entry_structure_type

        assert _entry_structure_type(GoldenQA(question='q', doc_code='X')) == 'structured'
        assert _entry_structure_type(GoldenQA(
            question='q', doc_code='X', structure_type='unstructured',
        )) == 'unstructured'

    def test_golden_set_covers_both_types(self):
        from app.evals.golden_qa import GOLDEN_QA

        types = {getattr(e, 'structure_type', 'structured') for e in GOLDEN_QA}
        assert 'structured' in types and 'unstructured' in types
