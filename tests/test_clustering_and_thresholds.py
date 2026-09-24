"""P2 #5 + #6 — Semantic clustering/batched titles + per-type thresholds.

#5: LLMClient.generate_batched_section_titles (one call titles all sections),
    ChunkingService._split_by_semantic_clustering (greedy agglomerative
    clustering on embeddings), _split_blind degradation, and the
    _split_by_headings rewire.
#6: classify_structure_type, per-type thresholds in the real hybrid impl
    (via monkeypatched constants — verifies wiring, not the exact numbers).
"""
import pytest

from unittest.mock import MagicMock

from app.utils.structure_type import classify_structure_type


# ── P2 #6: classifier ───────────────────────────────────────────────────────


class TestClassifyStructureType:
    def test_numbered_titles_are_structured(self):
        parents = [{'title': f'{i}.1 Section {i}', 'chapter_num': 1} for i in range(1, 6)]
        assert classify_structure_type(parents) == 'structured'

    def test_chapter_markers_are_structured(self):
        parents = [{'title': 'Some Topic', 'chapter_num': 2} for _ in range(4)]
        assert classify_structure_type(parents) == 'structured'

    def test_cluster_titles_are_unstructured(self):
        parents = [{'title': f'Cluster {i}'} for i in range(5)]
        assert classify_structure_type(parents) == 'unstructured'

    def test_blind_llm_titles_are_unstructured(self):
        parents = [{'title': 'Mixing Operation Details'}, {'title': 'Media Fill Process'}]
        assert classify_structure_type(parents) == 'unstructured'

    def test_empty_is_unstructured(self):
        assert classify_structure_type([]) == 'unstructured'

    def test_minority_markers_stay_unstructured(self):
        # 1 of 6 marked — below the 70% gate.
        parents = [{'title': 'Real Heading', 'chapter_num': 1}] + [
            {'title': f'Topic {i}'} for i in range(5)]
        assert classify_structure_type(parents) == 'unstructured'


# ── P2 #6: per-type thresholds ──────────────────────────────────────────────


class TestPerTypeThresholds:
    def test_mapping_lookup(self):
        from app.services.rag_service import _structure_thresholds
        from app.utils.constants import (
            MIN_COSINE_FLOOR_BY_TYPE, RELEVANCE_THRESHOLD_BY_TYPE,
            STRUCTURE_STRUCTURED, STRUCTURE_UNSTRUCTURED,
        )

        s = type('D', (), {'structure_type': STRUCTURE_STRUCTURED})()
        u = type('D', (), {'structure_type': STRUCTURE_UNSTRUCTURED})()
        assert _structure_thresholds(s) == (
            RELEVANCE_THRESHOLD_BY_TYPE[STRUCTURE_STRUCTURED],
            MIN_COSINE_FLOOR_BY_TYPE[STRUCTURE_STRUCTURED],
        )
        assert _structure_thresholds(u)[0] < _structure_thresholds(s)[0]

    def test_none_doc_uses_global_defaults(self):
        from app.services.rag_service import _structure_thresholds, RELEVANCE_THRESHOLD, MIN_COSINE_FLOOR
        assert _structure_thresholds(None) == (RELEVANCE_THRESHOLD, MIN_COSINE_FLOOR)

    def test_legacy_row_treated_as_unknown(self):
        from app.services.rag_service import _structure_thresholds, RELEVANCE_THRESHOLD, MIN_COSINE_FLOOR
        legacy = type('D', (), {'structure_type': None})()
        assert _structure_thresholds(legacy) == (RELEVANCE_THRESHOLD, MIN_COSINE_FLOOR)

    def test_impl_floor_relaxed_for_unstructured(self, monkeypatch):
        """Drive the REAL _retrieve_chunks_hybrid_impl: a chunk whose cosine sits
        between the relaxed (0.10) and strict (0.20) floors survives for an
        unstructured document and dies for a structured one."""
        from app.services import rag_service as rs

        def _make(chunk_cos_vec):
            svc = rs.RagService.__new__(rs.RagService)
            svc.user_id = 0
            svc.embedding_client = type('E', (), {
                'embed_text': staticmethod(lambda t: [0.1, 0.2, 0.3]),
                # E-2 is mode-aware: hash-mode clients compare docs against
                # get_hash_version(), so the fake must declare its mode + tag.
                'use_real': False,
                'get_model_version': staticmethod(lambda: 'stub-v1'),
                'get_hash_version': staticmethod(lambda: 'stub-v1'),
            })()
            chunk = type('C', (), {})()
            chunk.id = 1
            chunk.content = 'hold time acceptance criteria'
            chunk.page_no = 3
            chunk.chunk_index = 0
            chunk.embedding = chunk_cos_vec  # ~0.14 cosine with the query
            chunk.contextual_header = None

            doc = type('D', (), {
                'id': 1, 'code': 'SOP-T', 'is_latest': True, 'version': 1,
                'embedding_model_version': 'stub-v1',
            })()
            db = MagicMock()
            db.query.return_value = MagicMock(
                options=lambda *a, **k: db.query.return_value,
                filter=lambda *a, **k: db.query.return_value,
                order_by=lambda *a, **k: db.query.return_value,
                limit=lambda n: db.query.return_value,
                all=lambda: [chunk],
                first=lambda: doc,
            )
            svc.db = db
            # Per-test structure type is patched onto the doc double below.
            return svc, doc, chunk

        monkeypatch.setattr(rs, 'MIN_COSINE_FLOOR_BY_TYPE', {
            'structured': 0.20, 'unstructured': 0.10, 'unknown': 0.20,
        }, raising=False)

        low_vec = [0.3, -0.2, 0.1]  # cos ≈ 0.14 vs the query vec

        svc_u, doc_u, _ = _make(low_vec)
        doc_u.structure_type = 'unstructured'
        scored_u = svc_u._retrieve_chunks_hybrid_impl(1, 'hold time acceptance', top_k=3)
        assert scored_u, '0.14 cosine must survive the relaxed unstructured floor'

        svc_s, doc_s, _ = _make(low_vec)
        doc_s.structure_type = 'structured'
        scored_s = svc_s._retrieve_chunks_hybrid_impl(1, 'hold time acceptance', top_k=3)
        assert scored_s == [], '0.14 cosine must die at the strict structured floor'

    def test_retrieve_chunks_relaxed_relevance_for_unstructured(self, monkeypatch):
        """retrieve_chunks (the QA entry gate) applies the per-type blended
        threshold: a 0.6-blended chunk passes for unstructured, dies for
        structured."""
        from app.services import rag_service as rs

        svc = rs.RagService.__new__(rs.RagService)
        svc.user_id = 0
        svc.embedding_client = type('E', (), {
            'embed_text': staticmethod(lambda t: [0.1, 0.2, 0.3]),
            # E-2 is mode-aware: hash-mode clients compare docs against
            # get_hash_version(), so the fake must declare its mode + tag.
            'use_real': False,
            'get_model_version': staticmethod(lambda: 'stub-v1'),
            'get_hash_version': staticmethod(lambda: 'stub-v1'),
        })()

        chunk = type('C', (), {})()
        chunk.id = 1
        chunk.content = 'hold time acceptance criteria'
        chunk.page_no = 3
        chunk.chunk_index = 0
        chunk.embedding = [0.1, 0.2, 0.3]  # cosine 1.0 → blended ≈ 0.6
        chunk.contextual_header = None

        doc = type('D', (), {
            'id': 1, 'code': 'SOP-T', 'is_latest': True, 'version': 1,
            'embedding_model_version': 'stub-v1',
        })()
        db = MagicMock()
        db.query.return_value = MagicMock(
            options=lambda *a, **k: db.query.return_value,
            filter=lambda *a, **k: db.query.return_value,
            order_by=lambda *a, **k: db.query.return_value,
            limit=lambda n: db.query.return_value,
            all=lambda: [chunk],
            first=lambda: doc,
        )
        svc.db = db

        monkeypatch.setattr(rs, 'RELEVANCE_THRESHOLD_BY_TYPE', {
            'structured': 0.90, 'unstructured': 0.10, 'unknown': 0.90,
        }, raising=False)
        monkeypatch.setattr(rs, 'MIN_COSINE_FLOOR_BY_TYPE', {
            'structured': 0.0, 'unstructured': 0.0, 'unknown': 0.0,
        }, raising=False)

        doc.structure_type = 'unstructured'
        assert svc.retrieve_chunks(1, 'hold time acceptance', top_k=3), \
            '0.6 blended passes the relaxed unstructured gate'

        doc.structure_type = 'structured'
        assert svc.retrieve_chunks(1, 'hold time acceptance', top_k=3) == [], \
            '0.6 blended dies at the strict structured gate'


# ── P2 #5: batched titles ───────────────────────────────────────────────────


class _Resp:
    def __init__(self, text):
        self.text = text


class _Gen:
    def __init__(self):
        pass

    def update(self, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestBatchedTitles:
    def _client(self, reply_lines=None, raise_exc=None):
        from app.clients.llm_client import LLMClient

        svc = LLMClient.__new__(LLMClient)
        svc.model = 'gemini-test'
        if raise_exc:
            svc._client = MagicMock()
            svc._client.models.generate_content.side_effect = raise_exc
        else:
            svc._client = MagicMock()
            svc._client.models.generate_content.return_value = _Resp(
                '\n'.join(reply_lines or []))
        svc._llm_observation = lambda *a, **k: _Gen()
        svc._usage_details = lambda r, p, t: {}
        svc._log_tokens = lambda *a, **k: None
        return svc

    def test_parses_numbered_lines(self):
        svc = self._client(['0: Media Fill Process', '1: Line Clearance', 'junk line', '2: Deviation Handling'])
        out = svc.generate_batched_section_titles(['media fill text', 'clearance text', 'deviation text'])
        assert out == ['Media Fill Process', 'Line Clearance', 'Deviation Handling']

    def test_shape_always_matches_input(self):
        svc = self._client(['0: Only First'])  # LLM skipped the rest
        out = svc.generate_batched_section_titles(['alpha beta gamma', 'delta epsilon', 'zeta'])
        assert len(out) == 3
        assert out[0] == 'Only First'
        assert out[1] == 'delta epsilon'      # first-word fallback
        assert out[2] == 'zeta'

    def test_no_model_returns_fallbacks(self):
        from app.clients.llm_client import LLMClient
        svc = LLMClient.__new__(LLMClient)
        svc.model = None
        out = svc.generate_batched_section_titles(['alpha beta gamma delta', 'x'])
        assert out == ['alpha beta gamma delta', 'x']

    def test_api_failure_returns_fallbacks(self):
        svc = self._client(raise_exc=RuntimeError('api down'))
        out = svc.generate_batched_section_titles(['alpha beta gamma', 'other words here'])
        assert len(out) == 2
        assert all(out), 'fallbacks must be non-empty'

    def test_out_of_range_indices_ignored(self):
        svc = self._client(['0: Good Title', '99: Out Of Range', '-1: Negative'])
        out = svc.generate_batched_section_titles(['alpha beta gamma'])
        assert out == ['Good Title']


# ── P2 #5: semantic clustering ──────────────────────────────────────────────


def _fake_vec(text):
    """Deterministic topic vector: same TOPIC → same hot dimension."""
    topic = text.split()[0].strip()  # paragraphs start with 'topicN'
    h = sum(ord(c) for c in topic) % 7
    return [1.0 if i == h else 0.05 for i in range(8)]


class _FakeEmbedClient:
    use_real = True

    def embed_text(self, text):
        return _fake_vec(text)

    def get_model_version(self):
        return 'stub-v1'


class _ClusterLLM:
    """Has embedding_client + batched titles (clustering happy path)."""

    def __init__(self):
        self.embedding_client = _FakeEmbedClient()
        self.titled = False

    def generate_batched_section_titles(self, excerpts):
        self.titled = True
        return [f'Title {e.split(":")[0]}' for e in excerpts]


class TestSemanticClustering:
    def _service(self):
        from app.services.chunking_service import ChunkingService
        return ChunkingService(parent_target_tokens=200, child_target_tokens=60)

    def _text(self, topics=3, paras_per_topic=4):
        paras = []
        for t in range(topics):
            for p in range(paras_per_topic):
                paras.append(f'topic{t} paragraph {p} with detailed procedural content and specifics')
        return '\n\n'.join(paras)

    def test_clusters_split_at_topic_boundaries(self):
        svc = self._service()
        llm = _ClusterLLM()
        sections = svc._split_by_semantic_clustering(self._text(3, 4), [], llm)
        assert llm.titled, 'batched titles should have been called once'
        assert len(sections) == 3, 'three distinct topics → three sections'
        # Topic membership: each section contains only its own topic.
        for i, sec in enumerate(sections):
            assert f'topic{i}' in sec['content']
            assert f'topic{(i + 1) % 3}' not in sec['content']

    def test_titles_come_from_batched_call(self):
        svc = self._service()
        sections = svc._split_by_semantic_clustering(self._text(2, 3), [], _ClusterLLM())
        assert all(sec['title'].startswith('Title topic') for sec in sections)

    def test_oversized_cluster_split_to_token_band(self):
        svc = self._service()
        llm = _ClusterLLM()
        # One topic only, but huge → must be split near parent_target_tokens.
        paras = [f'topic0 paragraph {i} with detailed procedural content and specifics' for i in range(40)]
        sections = svc._split_by_semantic_clustering('\n\n'.join(paras), [], llm)
        assert len(sections) >= 2
        assert all(svc.count_tokens(s['content']) <= 2 * svc.parent_target_tokens for s in sections)

    def test_no_embedding_client_falls_back_to_blind(self):
        svc = self._service()

        class _NoEmbedLLM:
            embedding_client = None

            def generate_section_title(self, content):
                return 'Blind Title'

        sections = svc._split_by_semantic_clustering(self._text(2, 2), [], _NoEmbedLLM())
        assert sections == []  # signal for the caller to use the blind path

    def test_hash_embedder_not_used_for_clustering(self):
        """use_real=False (hash fallback) → refuse to cluster on fake vectors."""
        svc = self._service()

        class _HashEmbed:
            use_real = False

        class _LLM:
            embedding_client = _HashEmbed()

        assert svc._split_by_semantic_clustering(self._text(2, 2), [], _LLM()) == []

    def test_blind_path_still_works_directly(self):
        svc = self._service()
        sections = svc._split_blind(self._text(3, 4), [], None)
        assert len(sections) >= 1
        assert all('title' in s and 'page_start' in s for s in sections)

    def test_headings_path_uses_cluster_fallback(self, monkeypatch):
        """_split_by_headings with no headings → clustering first, blind on refusal."""
        svc = self._service()
        llm = _ClusterLLM()
        sections = svc._split_by_headings(self._text(2, 4), [], llm_client=llm, pages=[])
        assert llm.titled
        assert len(sections) == 2

    def test_numbered_docs_bypass_clustering(self):
        """A numbered document must NOT go through clustering."""
        svc = self._service()
        llm = _ClusterLLM()
        text = '\n\n'.join(
            f'{i}.{j} Section {i}{j}\n\nContent for section {i}{j} with enough words to be a real paragraph here.'
            for i in range(1, 3) for j in range(1, 3)
        )
        sections = svc._split_by_headings(text, [], llm_client=llm, pages=[])
        assert not llm.titled, 'numbered docs take the deterministic path'


# ── P2 #5 + #6: end-to-end split_pages ─────────────────────────────────────


class TestSplitPagesIntegration:
    def test_split_pages_renumbers_and_no_llm_per_section(self):
        from app.services.chunking_service import ChunkingService

        svc = ChunkingService(parent_target_tokens=200, child_target_tokens=60)
        llm = _ClusterLLM()
        text = '\n\n'.join(
            f'topic{t} paragraph {p} with detailed procedural content and specifics'
            for t in range(2) for p in range(4)
        )
        pages = [{'page_no': 1, 'text': text, 'format': 'plain'}]
        result = svc.split_pages(pages, toc=None, llm_client=llm)
        assert llm.titled
        parents = result['parents']
        assert len(parents) == 2
        # P2 #5: section_index is 1..N contiguous (renumbered once, globally).
        assert [p['section_index'] for p in parents] == [1, 2]
        # Classifier contract: cluster parents are unlabeled → unstructured.
        from app.utils.structure_type import classify_structure_type
        assert classify_structure_type(parents) == 'unstructured'

    def test_classify_numbered_split_pages(self):
        from app.services.chunking_service import ChunkingService
        from app.utils.structure_type import classify_structure_type

        svc = ChunkingService()
        body = 'Detailed section body with sufficient content for a parent. ' * 6
        text = '\n\n'.join(
            f'1.{i} Purpose {i}\n\n{body}' for i in range(1, 4)
        )
        result = svc.split_pages(
            [{'page_no': 1, 'text': text, 'format': 'plain'}], toc=None, llm_client=None)
        assert result['parents'], 'sections must survive the ghost filter'
        assert classify_structure_type(result['parents']) == 'structured'
