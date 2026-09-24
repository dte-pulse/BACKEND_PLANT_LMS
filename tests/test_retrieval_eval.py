"""
Retrieval eval harness + BM25 cache tests (P2 #9, P0 #4).
"""
import pytest


class TestGoldenDataset:
    def test_dataset_shape(self):
        from app.evals.golden_qa import GOLDEN_QA
        assert len(GOLDEN_QA) >= 10
        for e in GOLDEN_QA:
            assert e.question and e.doc_code
            if not e.out_of_scope:
                assert e.expected_keywords, 'in-scope entries need keywords to judge retrieval'

    def test_has_out_of_scope_entries(self):
        from app.evals.golden_qa import GOLDEN_QA
        assert any(e.out_of_scope for e in GOLDEN_QA)


class TestEvaluateRetrieval:
    def _chunk(self, content, page_no=1):
        from types import SimpleNamespace
        return SimpleNamespace(id=1, content=content, page_no=page_no)

    def test_in_scope_pass(self):
        from app.evals.retrieval_eval import evaluate_retrieval
        from app.evals.golden_qa import GoldenQA
        e = GoldenQA(question='q', doc_code='SOP-001', expected_keywords=['rinse', 'water'])
        ok, _ = evaluate_retrieval([(0.9, self._chunk('Final rinse water must meet spec.'))], e)
        assert ok

    def test_in_scope_fail_on_missing_keyword(self):
        from app.evals.retrieval_eval import evaluate_retrieval
        from app.evals.golden_qa import GoldenQA
        e = GoldenQA(question='q', doc_code='SOP-001', expected_keywords=['rinse', 'water'])
        ok, _ = evaluate_retrieval([(0.9, self._chunk('Final rinse must meet spec.'))], e)
        assert not ok

    def test_out_of_scope_passes_on_empty_results(self):
        from app.evals.retrieval_eval import evaluate_retrieval
        from app.evals.golden_qa import GoldenQA
        e = GoldenQA(question='q', doc_code='SOP-001', out_of_scope=True)
        ok, _ = _run(e)
        assert ok

    def test_out_of_scope_fails_when_chunk_returned(self):
        from app.evals.retrieval_eval import evaluate_retrieval
        from app.evals.golden_qa import GoldenQA
        e = GoldenQA(question='q', doc_code='SOP-001', out_of_scope=True)
        ok, _ = evaluate_retrieval([(0.9, self._chunk('anything'))], e)
        assert not ok

    def test_page_hint_enforced(self):
        from app.evals.retrieval_eval import evaluate_retrieval
        from app.evals.golden_qa import GoldenQA
        e = GoldenQA(question='q', doc_code='SOP-001', expected_keywords=['rinse'], expected_pages=[3])
        ok, _ = evaluate_retrieval([(0.9, self._chunk('rinse water', page_no=1))], e)
        assert not ok


def _run(e):
    from app.evals.retrieval_eval import evaluate_retrieval
    return evaluate_retrieval([], e)


class TestSyntheticHarness:
    def test_synthetic_run_meets_gate(self):
        from app.evals.retrieval_eval import run_synthetic
        report = run_synthetic()
        assert report['mode'] == 'synthetic'
        # The corpus is built FROM the golden entries, so a healthy pipeline
        # must score 100%; anything less means the gates are miscalibrated.
        assert report['accuracy'] >= 0.85, report['failures']
        assert report['passed'] == report['total']


class TestBM25Cache:
    def _chunks(self, contents):
        from types import SimpleNamespace
        return [SimpleNamespace(id=i + 1, content=c, embedding=None,
                                page_no=1, chunk_index=i + 1)
                for i, c in enumerate(contents)]

    def test_index_cached_per_version(self):
        from app.services import rag_service as rs
        from rank_bm25 import BM25Okapi

        rs._BM25_CACHE.clear()
        chunks = self._chunks(['cleaning validation procedure', 'line clearance steps'])

        idx1 = rs._get_bm25_index(5, 1, chunks)
        idx2 = rs._get_bm25_index(5, 1, chunks)
        assert idx1 is idx2  # same version → same cached index

        idx3 = rs._get_bm25_index(5, 2, chunks)  # version bump → rebuild
        assert idx3 is not idx1

    def test_invalidate_drops_entry(self):
        from app.services import rag_service as rs

        rs._BM25_CACHE.clear()
        chunks = self._chunks(['alpha beta', 'gamma delta'])
        rs._get_bm25_index(7, 1, chunks)
        assert 7 in rs._BM25_CACHE
        rs.invalidate_bm25_cache(7)
        assert 7 not in rs._BM25_CACHE

    def test_lru_eviction_cap(self):
        from app.services import rag_service as rs

        rs._BM25_CACHE.clear()
        chunks = self._chunks(['alpha beta', 'gamma delta'])
        for doc_id in range(30):
            rs._get_bm25_index(doc_id, 1, chunks)
        assert len(rs._BM25_CACHE) <= rs._BM25_CACHE_MAX

    def test_scores_reusable_after_cache_hit(self):
        from app.services import rag_service as rs

        rs._BM25_CACHE.clear()
        # 4-doc corpus: with N=2 rank_bm25's idf is exactly log(1)=0 for a
        # term in half the corpus, so scores would all be 0.0.
        chunks = self._chunks([
            'cleaning validation procedure',
            'media fill process',
            'annual product review',
            'deviation escalation matrix',
        ])
        idx = rs._get_bm25_index(9, 1, chunks)
        scores = list(idx.get_scores(rs._bm25_tokenize('cleaning validation')))
        assert len(scores) == 4
        assert scores[0] > scores[1]  # matching doc outranks non-matching
