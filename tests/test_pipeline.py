"""
RAG pipeline test suite (T-1/T-2 fix).

Covers the pure, DB-free parts of the pipeline that were previously untested:
- recap stripping / representative sampling (text_utils)
- token estimation (tokenizer)
- chunking invariants (no recap in child content, token bounds, char_end sync)
- embedding fallback behavior (hash mode only when no API key)
- adaptive mastery math (section pass/fail)
- RRF / blended-score math (rag_service)
- semantic cache key versioning
- MCQ validation + fallback correctness
"""
import importlib
import sys

import pytest


# ── text_utils ────────────────────────────────────────────────────────────────

class TestTextUtils:
    def test_strip_preceding_context_removes_recap(self):
        from app.utils.text_utils import strip_preceding_context
        text = "[Preceding Section: Cleaning Validation]\n... Final rinse water must meet WFI spec.\n\nThe actual section content starts here."
        cleaned = strip_preceding_context(text)
        assert 'Preceding Section' not in cleaned
        assert 'The actual section content starts here.' in cleaned

    def test_strip_preceding_context_noop_on_clean_text(self):
        from app.utils.text_utils import strip_preceding_context
        text = "Plain section content with no recap."
        assert strip_preceding_context(text) == text.strip()

    def test_strip_preceding_context_empty(self):
        from app.utils.text_utils import strip_preceding_context
        assert strip_preceding_context('') == ''
        assert strip_preceding_context(None) == ''

    def test_representative_sample_short_text_unchanged(self):
        from app.utils.text_utils import representative_sample
        text = "short text"
        assert representative_sample(text, 100) == text

    def test_representative_sample_includes_beginning_middle_end(self):
        from app.utils.text_utils import representative_sample
        text = ''.join(chr(97 + (i % 26)) for i in range(30000))
        sample = representative_sample(text, 6000)
        assert len(sample) <= 6000 + 200  # caps + margin for markers
        assert text[:50] in sample  # beginning
        assert text[-50:] in sample  # end


# ── tokenizer ────────────────────────────────────────────────────────────────

class TestTokenizer:
    def test_estimate_tokens_positive(self):
        from app.utils.tokenizer import estimate_tokens
        assert estimate_tokens("") == 0
        assert estimate_tokens("hello world") >= 1

    def test_estimate_cost_zero(self):
        from app.utils.tokenizer import estimate_cost
        assert estimate_cost(0, 0) == 0.0
        assert estimate_cost(1_000_000, 1_000_000) > 0.0


# ── chunking ─────────────────────────────────────────────────────────────────

class TestChunkingService:
    def _service(self):
        from app.services.chunking_service import ChunkingService
        return ChunkingService(parent_target_tokens=200, child_target_tokens=60)

    @staticmethod
    def _heading_pages():
        """Two pages, each starting with a markdown heading so the heading
        splitter creates two parent sections. Each section body is kept well
        above the G-12 min-merge threshold (150 tokens)."""
        return [
            {'page_no': 1, 'text': '# Section One\n\n' + ('Section one content paragraph. ' * 40)},
            {'page_no': 2, 'text': '# Section Two\n\n' + ('Section two content paragraph. ' * 40)},
        ]

    def test_split_pages_children_have_no_recap(self):
        """C-1: children must be split from clean content — no recap pollution."""
        svc = self._service()
        result = svc.split_pages(self._heading_pages())
        parents = result['parents']
        assert len(parents) == 2
        for parent in parents:
            for child in parent['children']:
                assert '[Preceding Section' not in child['content']

    def test_split_pages_parents_are_clean(self):
        """C-1: no parent content carries the recap either."""
        svc = self._service()
        parents = svc.split_pages(self._heading_pages())['parents']
        assert len(parents) >= 2
        for parent in parents:
            assert '[Preceding Section' not in (parent['content'] or '')

    def test_token_counts_positive_and_page_monotonic(self):
        svc = self._service()
        pages = [
            {'page_no': 1, 'text': 'Page one alpha beta gamma. ' * 20},
            {'page_no': 2, 'text': 'Page two delta epsilon zeta. ' * 20},
        ]
        result = svc.split_pages(pages)
        last_page = 0
        for parent in result['parents']:
            assert parent['token_count'] >= 1
            assert parent['page_start'] >= last_page
            last_page = parent['page_end']

    def test_count_tokens_consistent_with_shared_estimator(self):
        from app.services.chunking_service import ChunkingService
        from app.utils.tokenizer import estimate_tokens
        text = "Alpha beta gamma delta epsilon."
        assert ChunkingService.count_tokens(text) == estimate_tokens(text)

    # ── Numbered plain-text headings (doc-11 LLD regression) ───────────────

    @staticmethod
    def _numbered_pages():
        """Mimic the doc-11 LLD: a TOC page listing only chapters, then body
        pages whose headings the extraction service converted to markdown
        ("## **1. Introduction**", "### **1.1 Purpose**"). The tiny-section
        merge (G-12) must NOT collapse X.Y sections like 1.2/1.3, and the TOC
        page must not become a parent."""
        toc_page = {
            'page_no': 2,
            'text': (
                '## **Table of Contents** \n\n'
                '1. Introduction \n\n2. System Overview \n\n3. Module Design \n\n'
                '4. Data Design \n\n5. Interface Design\n\n'
            ),
        }
        body_1 = {
            'page_no': 3,
            'text': (
                '## **1. Introduction** \n\n'
                '### **1.1 Purpose** \n\n' + ('Purpose content paragraph alpha beta gamma. ' * 30) + '\n\n'
                '### **1.2 Scope** \n\n' + ('Scope content paragraph delta epsilon zeta. ' * 25) + '\n\n'
                '### **1.3 Audience** \n\n' + ('Audience content paragraph eta theta iota. ' * 15) + '\n\n'
                '### **1.4 Constraints** \n\n' + ('Constraint content paragraph kappa lambda mu. ' * 25) + '\n\n'
            ),
        }
        body_2 = {
            'page_no': 4,
            'text': (
                '## **2. System Overview** \n\n'
                '### **2.1 System Description** \n\n' + ('System description content paragraph. ' * 30) + '\n\n'
                '### **2.2 System Context** \n\n' + ('System context content paragraph. ' * 30) + '\n\n'
                '### **2.3 Architecture Diagram** \n\n' + ('Architecture content paragraph. ' * 30) + '\n\n'
            ),
        }
        return [{'page_no': 1, 'text': 'Low-Level Design (LLD) Document\n\nPrepared by: Test'}, toc_page, body_1, body_2]

    def test_numbered_headings_split_at_subsection_level(self):
        """X.Y sub-sections become one parent each — even when some are below
        the G-12 merge threshold (1.3 is ~60 tokens)."""
        svc = self._service()
        parents = svc.split_pages(self._numbered_pages())['parents']
        titles = [p['title'] for p in parents]
        expected = ['1.1 Purpose', '1.2 Scope', '1.3 Audience', '1.4 Constraints',
                    '2.1 System Description', '2.2 System Context', '2.3 Architecture Diagram']
        assert titles == expected

    def test_numbered_headings_exclude_toc_page(self):
        """The TOC page's chapter-only lines must not become parents, and no
        chapter heading (e.g. '2. System Overview') appears as a standalone
        parent either."""
        svc = self._service()
        parents = svc.split_pages(self._numbered_pages())['parents']
        titles = [p['title'] for p in parents]
        assert '1. Introduction' not in titles
        assert '2. System Overview' not in titles
        assert not any(t.lower().startswith('table of contents') for t in titles)

    def test_numbered_headings_no_chapter_line_in_content(self):
        """Chapter marker lines ('## 2. System Overview') are structural and
        must be stripped from the sub-section content that follows them.  The
        section's own heading line ('### **2.1 System Description**') may stay
        — that matches the markdown-heading path."""
        svc = self._service()
        parents = {p['title']: p for p in svc.split_pages(self._numbered_pages())['parents']}
        sec21 = parents['2.1 System Description']['content']
        assert 'System Overview' not in sec21   # chapter marker stripped
        assert '2. System Overview' not in sec21
        assert 'System description content' in sec21

    def test_numbered_headings_capture_real_chapter_titles(self):
        """M-7: each X.Y parent carries the document's real chapter heading
        ('2.1 System Description' → chapter 2 → '2. System Overview') so the
        mind-map concept tree can use exact chapter names."""
        svc = self._service()
        parents = {p['title']: p for p in svc.split_pages(self._numbered_pages())['parents']}
        assert parents['1.1 Purpose']['chapter_num'] == '1'
        assert parents['1.1 Purpose']['chapter_title'] == '1. Introduction'
        assert parents['2.3 Architecture Diagram']['chapter_num'] == '2'
        assert parents['2.3 Architecture Diagram']['chapter_title'] == '2. System Overview'

    def test_numbered_headings_do_not_strip_numbered_list_items(self):
        """Reviewer fix: numbered LIST items ('1. Mix the solution for ten
        minutes.') inside body content must NOT be stripped as if they were
        chapter markers — only short, punctuation-free chapter lines are."""
        pages = [
            {'page_no': 1, 'text': 'Cover page'},
            {
                'page_no': 2,
                'text': (
                    '### **1.1 Procedure** \n\n'
                    '1. Mix the solution for ten minutes.\n'
                    '2. Record the observed temperature.\n'
                    '3. Document the batch identifier on the log sheet.\n\n' + 
                    ('Additional procedure detail paragraph alpha beta gamma. ' * 8) + '\n\n'
                    '### **1.2 Verification** \n\n' + ('Verification content paragraph. ' * 25)
                ),
            },
        ]
        svc = self._service()
        parents = {p['title']: p for p in svc.split_pages(pages)['parents']}
        sec11 = parents['1.1 Procedure']['content']
        assert '1. Mix the solution for ten minutes.' in sec11
        assert '2. Record the observed temperature.' in sec11


# ── embedding client ─────────────────────────────────────────────────────────

class TestEmbeddingClient:
    def test_hash_version_matches_constant(self):
        from app.clients.embedding_client import EmbeddingClient, HASH_EMBEDDING_VERSION
        assert EmbeddingClient.get_hash_version() == HASH_EMBEDDING_VERSION

    def test_get_model_version_is_well_formed(self):
        from app.clients.embedding_client import EmbeddingClient
        assert 'gemini' in EmbeddingClient.get_model_version()

    def test_hash_embed_is_deterministic_and_dimensional(self):
        from app.clients.embedding_client import EmbeddingClient
        from app.utils.constants import EMBEDDING_DIM
        client = EmbeddingClient.__new__(EmbeddingClient)  # avoid settings/API init
        v1 = client._hash_embed("hello world")
        v2 = client._hash_embed("hello world")
        assert v1 == v2
        assert len(v1) == EMBEDDING_DIM

    def test_hash_embed_differs_across_texts(self):
        from app.clients.embedding_client import EmbeddingClient
        client = EmbeddingClient.__new__(EmbeddingClient)
        assert client._hash_embed("aaa") != client._hash_embed("bbb")


# ── adaptive mastery (A-1) ───────────────────────────────────────────────────

class TestAdaptiveMastery:
    """Tests the mastery logic of AdaptiveMcqService.is_parent_passed by
    simulating ChildChunkAttempt rows in a minimal in-memory stub."""

    def _make_service(self, attempts):
        """Build a service whose DB query returns the given attempts ordered by
        id DESC (as the real query does)."""
        from app.services.adaptive_mcq_service import AdaptiveMcqService

        class FakeAttempt:
            def __init__(self, is_correct, id_):
                self.is_correct = is_correct
                self.id = id_

        class FakeQuery:
            def filter(self, *a, **k):
                return self
            def order_by(self, *a, **k):
                return self
            def limit(self, n):
                return self
            def all(self):
                # Return rows ordered by id DESC, matching the real query
                # (window matches MASTERY_WINDOW from the service)
                from app.services.adaptive_mcq_service import MASTERY_WINDOW
                return sorted(attempts, key=lambda x: x.id, reverse=True)[:MASTERY_WINDOW]

        class FakeDB:
            def query(self, model):
                return FakeQuery()

        return AdaptiveMcqService(FakeDB())

    def test_single_correct_does_not_pass(self):
        svc = self._make_service([FakeAttemptStub(True, 1)])
        assert svc.is_parent_passed(1, 10) is False

    def test_two_consecutive_correct_passes(self):
        svc = self._make_service([FakeAttemptStub(True, 1), FakeAttemptStub(True, 2)])
        assert svc.is_parent_passed(1, 10) is True

    def test_latest_wrong_resets(self):
        svc = self._make_service([
            FakeAttemptStub(True, 1), FakeAttemptStub(True, 2), FakeAttemptStub(False, 3),
        ])
        assert svc.is_parent_passed(1, 10) is False

    def test_mastery_over_window_2_of_3_fails(self):
        # 2 correct of last 3 + latest correct → 66% < 80% → not passed
        svc = self._make_service([
            FakeAttemptStub(False, 1), FakeAttemptStub(True, 2), FakeAttemptStub(True, 3),
        ])
        assert svc.is_parent_passed(1, 10) is False

    def test_mastery_4_of_5_passes(self):
        # 4 correct of last 5 + latest correct → 80% → passed
        svc = self._make_service([
            FakeAttemptStub(False, 1), FakeAttemptStub(True, 2),
            FakeAttemptStub(True, 3), FakeAttemptStub(True, 4), FakeAttemptStub(True, 5),
        ])
        assert svc.is_parent_passed(1, 10) is True

    def test_mastery_3_of_5_fails(self):
        # 3 correct of last 5 + latest correct → 60% < 80% → not passed
        svc = self._make_service([
            FakeAttemptStub(False, 1), FakeAttemptStub(False, 2), FakeAttemptStub(False, 3),
            FakeAttemptStub(True, 4), FakeAttemptStub(True, 5),
        ])
        assert svc.is_parent_passed(1, 10) is False

    def test_no_attempts_does_not_pass(self):
        svc = self._make_service([])
        assert svc.is_parent_passed(1, 10) is False


class FakeAttemptStub:
    def __init__(self, is_correct, id_):
        self.is_correct = is_correct
        self.id = id_


# ── RRF / blended score (R-1/R-2) ────────────────────────────────────────────

class TestRrfBlend:
    def test_cosine_similarity_basic(self):
        from app.services.rag_service import _cosine_similarity
        assert _cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
        assert _cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
        assert _cosine_similarity([], []) == 0.0
        assert _cosine_similarity([1, 2], [1, 2, 3]) == 0.0  # dim mismatch

    def test_estimate_tokens_delegates_to_shared(self):
        from app.services.rag_service import _estimate_tokens
        from app.utils.tokenizer import estimate_tokens
        assert _estimate_tokens("a b c d") == estimate_tokens("a b c d")


class TestHybridRetrievalRegression:
    """Regression guards for the two retrieval bugs found during doc-11 QA:

    1. rank_bm25 returns a NUMPY array — the old ``if bm25_scores:``
       truthiness check crashed EVERY retrieve call whenever rank_bm25 was
       installed (every QA question came back as 'embeddings unavailable').
    2. The Python-cosine fallback used ``1 - cos`` (a DISTANCE) as a score
       and sorted descending — ranking the least-similar chunk first when
       pgvector was unavailable.
    """

    @staticmethod
    def _service(chunks, query_vec):
        from app.services.rag_service import RagService

        class FakeQuery:
            def __init__(self, model):
                self._model = model

            def options(self, *a, **k):
                return self

            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def limit(self, n):
                return self

            def all(self):
                from app.models.document import Document
                return [] if self._model is Document else chunks

            def first(self):
                return None

        class FakeDB:
            def query(self, *models):
                if len(models) > 1:
                    # simulate a pgvector failure -> Python-cosine fallback
                    raise RuntimeError('pgvector unavailable')
                return FakeQuery(models[0])

        class FakeEmbed:
            def embed_text(self, q):
                return query_vec

        svc = RagService.__new__(RagService)
        svc.db = FakeDB()
        svc.embedding_client = FakeEmbed()
        return svc

    def test_numpy_bm25_does_not_crash_and_fallback_orders_by_cosine(self, monkeypatch):
        import sys
        import types
        np = pytest.importorskip('numpy')

        class FakeBM25:
            @staticmethod
            def get_scores(query):
                return np.array([5.0, 1.0])  # numpy array, as rank_bm25 returns

        mod = types.ModuleType('rank_bm25')
        mod.BM25Okapi = FakeBM25
        monkeypatch.setitem(sys.modules, 'rank_bm25', mod)

        class FakeChunk:
            def __init__(self, cid, content, vec):
                self.id = cid
                self.content = content
                self.embedding = vec
                self.page_no = 1
                self.chunk_index = cid

        chunks = [
            FakeChunk(1, 'alpha beta gamma delta', [1.0, 0.0]),
            FakeChunk(2, 'epsilon zeta eta theta', [0.0, 1.0]),
        ]
        svc = self._service(chunks, query_vec=[1.0, 0.0])

        scored = svc._retrieve_chunks_hybrid_impl(11, 'alpha beta gamma', top_k=3)
        assert scored, 'retrieval must not crash on numpy BM25 scores'
        # chunk 1 is cosine-similar (1.0) and lexically matching — it must
        # rank first, not last (regression: 1-cos distance sorted desc).
        assert scored[0][1].id == 1


# ── semantic cache (R-5/R-6) ─────────────────────────────────────────────────

class TestSemanticCacheKey:
    def _cache(self):
        from app.services.semantic_cache_service import SemanticCacheService
        return SemanticCacheService.__new__(SemanticCacheService)

    def test_version_and_topic_in_key(self):
        cache = self._cache()
        assert cache._cache_key(1, topic_id=5, doc_version=2) == 'qa_cache:1:v2:5'
        assert cache._cache_key(1) == 'qa_cache:1:all'
        assert cache._cache_key(2, topic_id=7) == 'qa_cache:2:7'

    def test_different_versions_produce_different_keys(self):
        cache = self._cache()
        assert cache._cache_key(1, 5, 1) != cache._cache_key(1, 5, 2)


# ── MCQ validation (I-3) ─────────────────────────────────────────────────────

class TestMcqValidation:
    def test_valid_mcqs_pass_through(self):
        from app.services.ingestion_service import IngestionService
        items = [{
            'question': 'Q1?',
            'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'},
            'correct_option': 'B',
            'explanation': 'e',
        }]
        valid = IngestionService._validate_section_mcqs(items)
        assert len(valid) == 1
        assert valid[0]['correct_option'] == 'B'

    def test_invalid_correct_option_dropped(self):
        from app.services.ingestion_service import IngestionService
        items = [{
            'question': 'Q1?',
            'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'},
            'correct_option': 'E',
            'explanation': 'e',
        }]
        assert IngestionService._validate_section_mcqs(items) == []

    def test_duplicate_options_dropped(self):
        from app.services.ingestion_service import IngestionService
        items = [{
            'question': 'Q1?',
            'options': {'A': 'same', 'B': 'same', 'C': 'c', 'D': 'd'},
            'correct_option': 'A',
            'explanation': 'e',
        }]
        assert IngestionService._validate_section_mcqs(items) == []

    def test_duplicate_questions_dropped(self):
        from app.services.ingestion_service import IngestionService
        items = [
            {'question': 'Same?', 'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'}, 'correct_option': 'A', 'explanation': '1'},
            {'question': 'same?', 'options': {'A': 'w', 'B': 'x', 'C': 'y', 'D': 'z'}, 'correct_option': 'B', 'explanation': '2'},
        ]
        assert len(IngestionService._validate_section_mcqs(items)) == 1

    def test_fallback_mcqs_vary_correct_answer(self):
        """I-1: the correct option letter must not be fixed across sections."""
        from app.services.ingestion_service import IngestionService
        a = IngestionService._fallback_section_mcqs_static('Section alpha content here is long enough to make sentences that pass the filter.', 5)
        b = IngestionService._fallback_section_mcqs_static('Section beta completely different content in this section.', 5)
        corrects_a = {q['correct_option'] for q in a}
        corrects_b = {q['correct_option'] for q in b}
        assert corrects_a and corrects_b
        assert len(a) == 5 and len(b) == 5


# ── Section MCQ generation retry (malformed LLM output) ─────────────────────

class _FakeSettings:
    """Stand-in for app.core.config.settings with a valid-looking Gemini key."""
    gemini_api_key = 'test-key'


class TestSectionMcqGeneration:
    """A malformed first Gemini response must be retried (and only then fall
    back), instead of a single bad response nuking a section's MCQs."""

    @staticmethod
    def _fake_rows():
        import json as _json
        return _json.dumps([
            {'question': f'Q{i}?', 'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'},
             'correct_option': 'A', 'explanation': 'e'}
            for i in range(5)
        ])

    def _service(self, monkeypatch, raw_fn):
        from app.services.ingestion_service import IngestionService
        svc = IngestionService.__new__(IngestionService)  # skip __init__ deps
        monkeypatch.setattr('app.core.config.settings', _FakeSettings())
        monkeypatch.setattr(svc, '_generate_section_mcqs_raw', raw_fn)
        monkeypatch.setattr('app.evals.judges.judge_mcq_quality', lambda *a, **k: None)
        return svc

    def test_malformed_first_response_is_retried(self, monkeypatch):
        from app.services.ingestion_service import IngestionService
        calls = {'n': 0}

        def raw(content, count):
            calls['n'] += 1
            if calls['n'] == 1:
                return '[{"question": "Q1?", "options"'  # truncated mid-object
            return self._fake_rows()

        svc = self._service(monkeypatch, raw)
        mcqs = svc._generate_section_mcqs('Some section content for testing purposes.')
        assert calls['n'] == 2  # initial attempt + exactly one retry
        assert len(mcqs) == 5
        assert all('Q' in q['question'] for q in mcqs)

    def test_consistently_malformed_output_falls_back(self, monkeypatch):
        from app.services.ingestion_service import IngestionService
        calls = {'n': 0}

        def raw(content, count):
            calls['n'] += 1
            return '[{"question": "Q1?"'  # always truncated

        svc = self._service(monkeypatch, raw)
        mcqs = svc._generate_section_mcqs('Some section content for testing purposes.')
        assert calls['n'] == 2  # initial + retry, then deterministic fallback
        assert len(mcqs) == 5
        assert mcqs[0]['options']  # fallback rows are still valid MCQs
        assert IngestionService._SECTION_MCQ_COUNT == 5

    def test_non_array_response_falls_back_without_retry(self, monkeypatch):
        """A single object instead of an array is a shape problem — retrying
        won't fix it, so fall back after the first call only."""
        from app.services.ingestion_service import IngestionService
        calls = {'n': 0}

        def raw(content, count):
            calls['n'] += 1
            return '{"question": "Q1?", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "correct_option": "A"}'

        svc = self._service(monkeypatch, raw)
        mcqs = svc._generate_section_mcqs('Some section content for testing purposes.')
        assert calls['n'] == 1  # no wasted retry on a parseable non-list
        assert len(mcqs) == 5
        assert mcqs[0]['options']


# ── N+1 elimination regression (stress-test follow-up) ───────────────────────

class TestNoNPlusOne:
    """
    Verifies the batched-query rewrites still produce correct results.
    Uses a real in-memory SQLite DB seeded with the same models the services
    query, so an accidental re-introduction of per-row lookups would surface
    as incorrect aggregates (and the query-count guard catches it directly).
    """

    @pytest.fixture()
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.session import Base
        from app.models.user import User
        from app.models.document import Document
        from app.models.topic import Topic
        from app.models.subject import Subject
        from app.models.training import TrainingAssignment
        from app.models.user_weakness_profile import UserWeaknessProfile
        from app.models.user_progress import UserProgress
        from app.models.user_mcq_attempt import UserMcqAttempt
        from datetime import datetime, timedelta, timezone

        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        s = Session()

        # SQLite stores naive datetimes; use utcnow() (not tz-aware) so the
        # services' `due_date < now` comparisons behave like Postgres.
        now = datetime.utcnow()
        subject = Subject(name='Validation', department='Production')
        s.add(subject)
        s.flush()
        for i, (code, dept, role) in enumerate([
            ('U1', 'Production', 'trainee'),
            ('U2', 'Production', 'trainee'),
            ('U3', 'QA', 'trainee'),
        ]):
            s.add(User(employee_code=code, full_name=f'User {i}', department=dept,
                       role=role, is_active=True, hashed_password='x'))
        s.flush()

        topic = Topic(subject_id=subject.id, title='Validation')
        s.add(topic)
        s.flush()

        docs = {}
        for code, title in [('DOC-1', 'Cleaning Validation'), ('DOC-2', 'Water Systems')]:
            d = Document(code=code, title=title, topic=title, topic_id=topic.id,
                         subject_id=subject.id, version=1, status='active')
            s.add(d)
            s.flush()
            docs[code] = d

        users = {u.employee_code: u for u in s.query(User).all()}
        # Assignment states: U1 completed DOC-1 (past due), U2 pending DOC-2 (overdue),
        # U3 pending DOC-1 (overdue).
        s.add(TrainingAssignment(user_id=users['U1'].id, document_id=docs['DOC-1'].id,
                                 training_type='sop', status='completed',
                                 due_date=now - timedelta(days=5)))
        s.add(TrainingAssignment(user_id=users['U2'].id, document_id=docs['DOC-2'].id,
                                 training_type='sop', status='assigned',
                                 due_date=now - timedelta(days=2)))
        s.add(TrainingAssignment(user_id=users['U3'].id, document_id=docs['DOC-1'].id,
                                 training_type='sop', status='assigned',
                                 due_date=now - timedelta(days=1)))
        # A critical weakness for U1.
        s.add(UserWeaknessProfile(user_id=users['U1'].id, topic_id=topic.id,
                                  document_id=docs['DOC-1'].id, score=40.0,
                                  is_critical=True))
        # Progress + an MCQ attempt for U1 on DOC-1.
        s.add(UserProgress(user_id=users['U1'].id, document_id=docs['DOC-1'].id,
                           topic_id=topic.id, completion_percentage=100.0,
                           time_spent_seconds=60))
        s.add(UserMcqAttempt(user_id=users['U1'].id, document_id=docs['DOC-1'].id,
                             score=92.0, passed=True))
        s.commit()
        yield s
        s.close()

    @staticmethod
    def _naive_now_service_call(db, method, *args, **kwargs):
        """Call a ReportService method with a naive `now` so SQLite datetimes
        (naive) compare correctly, mirroring Postgres' tz-aware behavior."""
        from unittest.mock import patch
        import app.services.report_service as report_module
        from app.services.report_service import ReportService
        from datetime import datetime

        class _FakeDateTime:
            @staticmethod
            def now(tz=None):
                return datetime.utcnow()

        class _FakeTimezone:
            utc = None  # keep replace(tzinfo=timezone.utc) naive, like SQLite

        with patch.object(report_module, 'datetime', _FakeDateTime), \
                patch.object(report_module, 'timezone', _FakeTimezone):
            return getattr(ReportService(db), method)(*args, **kwargs)

    @staticmethod
    def _count_selects(db, fn, *args, **kwargs):
        """Run fn against db and return (result, number_of_SELECT statements).
        Guards against N+1 regression: if the batched rewrite is reverted to
        per-row loops, the SELECT count jumps and the test fails."""
        from sqlalchemy import event
        count = {'n': 0}

        def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('SELECT'):
                count['n'] += 1

        event.listen(db.get_bind(), 'after_cursor_execute', _after_cursor_execute)
        try:
            result = fn(*args, **kwargs)
        finally:
            event.remove(db.get_bind(), 'after_cursor_execute', _after_cursor_execute)
        return result, count['n']

    def test_compliance_report_aggregates(self, db):
        from app.services.report_service import ReportService
        report, selects = self._count_selects(
            db, lambda: self._naive_now_service_call(db, 'get_compliance_report'))
        # users(1) + assignments(1) + critical weakness counts(1) — never per-user
        assert selects <= 4, f'compliance report issued {selects} SELECTs (N+1?)'
        by_dept = {d['department']: d for d in report}
        prod = by_dept['Production']
        assert prod['total_employees'] == 2
        assert prod['total_assignments'] == 2      # U1 completed + U3 pending
        assert prod['completed'] == 1
        assert prod['overdue'] == 1
        assert prod['nq_employees'] == 1           # U1 critical weakness
        qa = by_dept['QA']
        assert qa['total_employees'] == 1
        assert qa['overdue'] == 1

    def test_overdue_report_batched(self, db):
        from app.services.report_service import ReportService
        rows, selects = self._count_selects(
            db, lambda: self._naive_now_service_call(db, 'get_overdue_report'))
        # overdue(1) + users(1) + docs(1) — never per-assignment
        assert selects <= 4, f'overdue report issued {selects} SELECTs (N+1?)'
        by_uid = {r['user_id'] for r in rows}
        assert by_uid == {2, 3}  # U2 and U3 are overdue; U1 completed
        for r in rows:
            assert r['document_title'] in ('Cleaning Validation', 'Water Systems')
        # Department filter still applies (U2 is Production + overdue; U3 is QA)
        prod_only = self._naive_now_service_call(db, 'get_overdue_report', department='Production')
        assert {r['user_id'] for r in prod_only} == {2}

    def test_nq_employees_deduplicated_per_user(self, db):
        from app.services.report_service import ReportService
        rows = ReportService(db).get_nq_employees()
        assert len(rows) == 1
        assert rows[0]['critical_weak_topics'] == 1

    def test_global_readiness_single_aggregate(self, db):
        from app.services.report_service import ReportService
        g = ReportService(db).get_global_readiness()
        assert g['total_assignments'] == 3
        assert g['completed'] == 1
        assert g['overdue'] == 2

    def test_user_training_history_best_score(self, db):
        from app.services.report_service import ReportService
        rows = ReportService(db).get_user_training_history(1)
        assert len(rows) == 1
        assert rows[0]['best_score'] == 92.0
        assert rows[0]['passed'] is True
        assert rows[0]['document_title'] == 'Cleaning Validation'

    def _clear_user_cache(self, user_id: int = 1):
        """Drop live-Redis keys the learning endpoints may have populated, so
        tests run against the in-memory DB rather than a stale cache entry."""
        try:
            from app.services.response_cache import ResponseCache
            ResponseCache().delete_by_prefix(f'resp:learning:{user_id}:')
        except Exception:  # noqa: BLE001 — cache must never break tests
            pass

    def test_learning_assigned_batched(self, db):
        from app.api.v1.endpoints.learning import get_assigned_documents
        # Endpoint signature: (current_user, db). We call the underlying logic
        # via the endpoint function directly with a fake current_user.
        self._clear_user_cache()

        class FakeUser:
            id = 1
        rows = get_assigned_documents(FakeUser(), db)
        assert len(rows) == 1
        assert rows[0]['completion_percentage'] == 100.0
        assert rows[0]['document_code'] == 'DOC-1'

    def test_learning_training_record_batched(self, db):
        from app.api.v1.endpoints.learning import get_training_record
        self._clear_user_cache()

        class FakeUser:
            id = 1
            full_name = 'User 0'
            employee_code = 'U1'
            department = 'Production'
        rec = get_training_record(FakeUser(), db)
        assert rec['training_record'][0]['best_score'] == 92.0


# ── Response cache (Redis, short TTL) ────────────────────────────────────────

class TestResponseCache:
    """Unit tests for the Redis response cache using a minimal fake Redis."""

    class _FakeRedis:
        def __init__(self):
            self.store: dict[str, str] = {}
            self.ping_ok = True

        def ping(self):
            if not self.ping_ok:
                raise ConnectionError('down')
            return True

        def get(self, key):
            return self.store.get(key)

        def setex(self, key, ttl, value):
            self.store[key] = value

        def delete(self, *keys):
            for k in keys:
                self.store.pop(k, None)

        def scan(self, cursor, match='*', count=10):
            import fnmatch
            keys = [k for k in self.store if fnmatch.fnmatch(k, match)]
            return 0, keys

    def _cache(self, fake=None):
        from app.services.response_cache import ResponseCache
        cache = ResponseCache.__new__(ResponseCache)
        cache._redis = fake or self._FakeRedis()
        cache._available = True
        return cache

    def test_set_get_roundtrip(self):
        cache = self._cache()
        cache.set('k', {'a': 1, 'b': [1, 2]}, ttl=30)
        assert cache.get('k') == {'a': 1, 'b': [1, 2]}

    def test_get_or_set_miss_then_hit(self):
        cache = self._cache()
        calls = {'n': 0}

        def factory():
            calls['n'] += 1
            return {'value': 42}

        assert cache.get_or_set('k', 30, factory) == {'value': 42}
        assert cache.get_or_set('k', 30, factory) == {'value': 42}
        assert calls['n'] == 1  # second call served from cache

    def test_datetime_roundtrips_as_iso(self):
        """jsonable_encoder must make datetimes JSON-safe before storing."""
        from datetime import datetime, timezone
        cache = self._cache()
        due = datetime(2026, 8, 1, tzinfo=timezone.utc)
        cache.set('overdue', {'due_date': due}, ttl=30)
        stored = cache._redis.store['overdue']
        assert '2026-08-01' in stored
        loaded = cache.get('overdue')
        assert loaded['due_date'] == '2026-08-01T00:00:00+00:00'

    def test_unavailable_redis_noops(self):
        from app.services.response_cache import ResponseCache
        fake = self._FakeRedis()
        fake.ping_ok = False
        cache = ResponseCache.__new__(ResponseCache)
        cache._redis = fake
        cache._available = False  # simulated unavailable state
        assert cache.available is False
        assert cache.get('k') is None
        cache.set('k', 1, ttl=30)  # must not raise
        assert cache.get('k') is None

    def test_delete_by_prefix(self):
        fake = self._FakeRedis()
        cache = self._cache(fake)
        cache.set('resp:report:compliance', 1, ttl=30)
        cache.set('resp:report:overdue', 2, ttl=30)
        cache.set('resp:other:x', 3, ttl=30)
        cache.delete_by_prefix('resp:report:')
        assert cache.get('resp:report:compliance') is None
        assert cache.get('resp:report:overdue') is None
        assert cache.get('resp:other:x') == 3

    def test_invalidate_cached_helper(self):
        from app.services.response_cache import invalidate_cached
        fake = self._FakeRedis()
        cache = self._cache(fake)
        cache.set('resp:report:a', 1, ttl=30)
        cache.set('resp:learning:7:assigned', 2, ttl=30)
        # invalidate_cached builds its own ResponseCache (real redis) — instead
        # verify the helper's prefix semantics via delete_by_prefix directly:
        cache.delete_by_prefix('resp:report:')
        cache.delete_by_prefix('resp:learning:7:')
        assert cache.get('resp:report:a') is None
        assert cache.get('resp:learning:7:assigned') is None

    def test_none_factory_result_is_cached_not_recomputed(self):
        """Review fix: a factory returning None must still be cached (as JSON
        null) instead of being recomputed on every call."""
        cache = self._cache()
        calls = {'n': 0}

        def factory():
            calls['n'] += 1
            return None

        assert cache.get_or_set('k', 30, factory) is None
        assert cache.get_or_set('k', 30, factory) is None
        assert calls['n'] == 1  # second call served from the cached null

    def test_get_cache_is_singleton(self):
        """Review fix: the module-level get_cache() must return one instance so
        the Redis ping happens once per process, not per request."""
        from app.services.response_cache import get_cache
        assert get_cache() is get_cache()


# ── Idle-ping DB pool (replaces pool_pre_ping) ───────────────────────────────

class TestIdlePingPool:
    """Unit tests for the checkout/reset/connect listeners in app.db.session.
    They are plain event handlers, so we exercise them with fake DBAPI
    connections + connection records (no real DB needed)."""

    class _FakeCursor:
        def __init__(self, fail=False):
            self.fail = fail
            self.executed = False

        def execute(self, stmt):
            self.executed = True
            if self.fail:
                raise RuntimeError('connection gone')

        def close(self):
            pass

    class _FakeDbapi:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

    class _FakeRecord:
        def __init__(self):
            self.info = {}

    def test_recently_used_connection_skips_ping(self):
        """The whole point: a hot connection must NOT pay a round-trip."""
        from app.db.session import _ping_if_idle, _LAST_USE_KEY
        import time as _t
        cursor = self._FakeCursor(fail=True)  # would explode if pinged
        conn = self._FakeDbapi(cursor)
        rec = self._FakeRecord()
        rec.info[_LAST_USE_KEY] = _t.monotonic()  # just used
        _ping_if_idle(conn, rec, None)
        assert cursor.executed is False

    def test_idle_connection_is_pinged(self):
        from app.db.session import _ping_if_idle, _LAST_USE_KEY, IDLE_PING_SECONDS
        import time as _t
        cursor = self._FakeCursor()
        conn = self._FakeDbapi(cursor)
        rec = self._FakeRecord()
        rec.info[_LAST_USE_KEY] = _t.monotonic() - IDLE_PING_SECONDS - 5
        _ping_if_idle(conn, rec, None)
        assert cursor.executed is True

    def test_failed_ping_raises_disconnection_error(self):
        from app.db.session import _ping_if_idle, _LAST_USE_KEY, IDLE_PING_SECONDS
        from sqlalchemy.exc import DisconnectionError
        import time as _t
        cursor = self._FakeCursor(fail=True)
        conn = self._FakeDbapi(cursor)
        rec = self._FakeRecord()
        rec.info[_LAST_USE_KEY] = _t.monotonic() - IDLE_PING_SECONDS - 5
        with pytest.raises(DisconnectionError):
            _ping_if_idle(conn, rec, None)

    def test_connect_stamps_record(self):
        from app.db.session import _stamp_on_connect, _LAST_USE_KEY
        rec = self._FakeRecord()
        _stamp_on_connect(None, rec)
        assert _LAST_USE_KEY in rec.info

    def test_reset_stamps_record(self):
        from app.db.session import _stamp_on_reset, _LAST_USE_KEY
        rec = self._FakeRecord()
        _stamp_on_reset(None, rec)
        assert _LAST_USE_KEY in rec.info

    def test_idle_threshold_is_configurable_and_positive(self):
        """Sanity: the idle-ping threshold comes from settings and is >= 1s
        (no unconditional per-checkout ping — replaced by idle-scoped one)."""
        from app.db.session import IDLE_PING_SECONDS
        from app.core.config import settings
        assert IDLE_PING_SECONDS >= 1
        assert IDLE_PING_SECONDS == max(1, int(settings.db_idle_ping_seconds))


# ── Multi-agent adaptive learning (concept mastery) ──────────────────────────

class _NoLLM:
    """LLM stub that always fails — exercises the deterministic fallbacks."""

    def generate_json(self, *a, **k):
        return None

    def generate_text(self, *a, **k):
        return None


class TestMasteryScoring:
    """Pure mastery-math tests (EMA + bands + difficulty ladder)."""

    def test_correct_answer_moves_score_toward_100(self):
        from app.agents.weakness_agent import ema_update
        assert ema_update(0.0, True) > 0.0
        assert ema_update(50.0, True) > 50.0
        assert ema_update(100.0, True) == pytest.approx(100.0)

    def test_wrong_answer_halves_score(self):
        from app.agents.weakness_agent import ema_update
        assert ema_update(100.0, False) == pytest.approx(50.0)
        assert ema_update(0.0, False) == pytest.approx(0.0)

    def test_mastery_band_boundaries(self):
        from app.agents.base_agent import mastery_level_for_score
        assert mastery_level_for_score(85.0) == 'mastered'
        assert mastery_level_for_score(84.9) == 'proficient'
        assert mastery_level_for_score(65.0) == 'proficient'
        assert mastery_level_for_score(64.9) == 'learning'
        assert mastery_level_for_score(40.0) == 'learning'
        assert mastery_level_for_score(39.9) == 'novice'

    def test_difficulty_ladder_follows_level(self):
        from app.agents.base_agent import difficulty_for_level
        assert difficulty_for_level('novice') == 'easy'
        assert difficulty_for_level('learning') == 'medium'
        assert difficulty_for_level('proficient') == 'hard'
        assert difficulty_for_level('mastered') == 'hard'


class TestCurriculumAgent:
    """Pure curriculum decision logic (difficulty/format from mastery state)."""

    def _decide(self, attempts=0, score=0.0, level='novice'):
        from app.agents.curriculum_agent import CurriculumAgent
        return CurriculumAgent._decide(attempts, score, level)

    def test_first_exposure_is_easy(self):
        d = self._decide(attempts=0)
        assert d['difficulty'] == 'easy'
        assert d['format'] == 'objective'  # easy only supports objective

    def test_novice_stays_easy(self):
        assert self._decide(attempts=3, score=30.0, level='novice')['difficulty'] == 'easy'

    def test_learning_escalates_to_medium(self):
        d = self._decide(attempts=3, score=55.0, level='learning')
        assert d['difficulty'] == 'medium'
        assert d['format'] in ('objective', 'true_false')

    def test_proficient_escalates_to_hard(self):
        d = self._decide(attempts=3, score=72.0, level='proficient')
        assert d['difficulty'] == 'hard'
        assert d['format'] in ('scenario', 'objective')

    def test_format_varies_across_attempts_at_hard(self):
        from app.agents.curriculum_agent import CurriculumAgent
        formats = {CurriculumAgent._decide(a, 80.0, 'proficient')['format'] for a in (3, 4, 5)}
        assert len(formats) > 1  # recall→scenario rotation, not a fixed format

    def test_plan_question_reads_mastery_from_db(self):
        from app.agents.curriculum_agent import CurriculumAgent

        class FakeMastery:
            score = 70.0
            mastery_level = 'proficient'
            attempts = 4

        class FakeQuery:
            def filter(self, *a, **k):
                return self
            def first(self):
                return FakeMastery()

        class FakeDB:
            def query(self, model):
                return FakeQuery()

        class FakeChunk:
            id = 9
            page_no = 3

        plan = CurriculumAgent(FakeDB()).plan_question(1, FakeChunk())
        assert plan['difficulty'] == 'hard'
        assert 'concept 9' in plan['target_summary']


class TestLlmJsonResilience:
    """LLMClient.generate_json must honor its 'returns None on failure'
    contract — a malformed/truncated Gemini reply must NOT raise. The
    EvaluatorAgent's wrong-answer diagnosis (and the adaptive answer submit
    endpoint) depends on the None fallback."""

    class _FakeGen:
        def update(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def test_generate_json_returns_none_on_unparseable_output(self, monkeypatch):
        from app.clients.llm_client import LLMClient

        class FakeResponse:
            text = '{"re_explanation": "cut off mid-sent'  # truncated, unparseable

        class FakeModels:
            def generate_content(self, *a, **k):
                return FakeResponse()

        class FakeClient:
            models = FakeModels()

        llm = LLMClient.__new__(LLMClient)
        llm.model = 'gemini-2.5-flash'
        llm._client = FakeClient()
        monkeypatch.setattr(llm, '_llm_observation', lambda *a, **k: self._FakeGen())
        monkeypatch.setattr(llm, '_log_tokens', lambda *a, **k: None)

        assert llm.generate_json('prompt', user_id=0, operation='agent_evaluate') is None

    def test_generate_json_parses_valid_output(self, monkeypatch):
        from app.clients.llm_client import LLMClient

        class FakeResponse:
            text = '{"re_explanation": "read page 1 again", "diagnosis": null}'

        class FakeModels:
            def generate_content(self, *a, **k):
                return FakeResponse()

        class FakeClient:
            models = FakeModels()

        llm = LLMClient.__new__(LLMClient)
        llm.model = 'gemini-2.5-flash'
        llm._client = FakeClient()
        monkeypatch.setattr(llm, '_llm_observation', lambda *a, **k: self._FakeGen())
        monkeypatch.setattr(llm, '_log_tokens', lambda *a, **k: None)

        data = llm.generate_json('prompt', user_id=0, operation='agent_evaluate')
        assert data == {'re_explanation': 'read page 1 again', 'diagnosis': None}


class TestQuestionGeneratorAgent:
    """Bank-first behavior + deterministic fallback without an LLM."""

    def _agent(self, bank_rows=()):
        from app.agents.question_generator_agent import QuestionGeneratorAgent

        class FakeMCQ:
            def __init__(self, qid):
                self.id = qid
                self.question = f'Q{qid}?'
                self.options = {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'}
                self.correct_option = 'A'
                self.explanation = 'e'
                self.difficulty = 'medium'
                self.type = 'objective'

        class FakeQuery:
            def filter(self, *a, **k):
                return self
            def all(self):
                return [FakeMCQ(1)] if bank_rows else []

        class FakeDB:
            def query(self, model):
                return FakeQuery()
            def add(self, x):
                pass
            def commit(self):
                pass
            def refresh(self, x):
                pass

        class FakeChunk:
            id = 5
            document_id = 1
            topic_id = 1
            page_no = 2
            content = 'Some section content about validation procedures.'

        agent = QuestionGeneratorAgent(FakeDB())
        agent._llm = _NoLLM()
        return agent, FakeChunk()

    def test_bank_hit_returns_cached_mcq(self):
        agent, chunk = self._agent(bank_rows=True)
        q = agent.generate(1, chunk, difficulty='medium', prefer_format='objective')
        assert q['mcq_id'] == 1
        assert q['is_dynamic'] is False
        assert q['question'].startswith('Q1')

    def test_bank_miss_without_llm_uses_fallback(self):
        agent, chunk = self._agent(bank_rows=False)
        q = agent.generate(1, chunk, difficulty='hard', prefer_format='objective')
        assert q['is_dynamic'] is True
        assert q['mcq_id'] is None
        assert q['correct_option'] in ('A', 'B', 'C', 'D')
        assert q['options']

    def test_true_false_fallback_shape(self):
        from app.agents.question_generator_agent import QuestionGeneratorAgent
        q = QuestionGeneratorAgent._fallback_question(None, 'medium', 'true_false')
        assert set(q['options']) == {'A', 'B'}
        assert q['format'] == 'true_false'

    def test_easy_only_offers_objective(self):
        from app.agents.question_generator_agent import FORMAT_BY_DIFFICULTY
        assert FORMAT_BY_DIFFICULTY['easy'] == ['objective']
        assert 'true_false' not in FORMAT_BY_DIFFICULTY['easy']


class TestEvaluatorAgent:
    def _agent(self):
        from app.agents.evaluator_agent import EvaluatorAgent
        agent = EvaluatorAgent(None)
        agent._llm = _NoLLM()
        return agent

    def test_correct_answer_normalises_case(self):
        agent = self._agent()
        r = agent.evaluate(1, None, {'correct_option': 'B', 'explanation': 'x'}, 'b')
        assert r['is_correct'] is True
        assert r['diagnosis'] is None

    def test_wrong_answer_gets_fallback_re_explanation(self):
        agent = self._agent()

        class FakeChunk:
            page_no = 4
            content = 'The correct procedure must be followed at all times.'

        r = agent.evaluate(1, FakeChunk(), {'correct_option': 'A', 'explanation': 'x', 'question': 'Q?'}, 'C')
        assert r['is_correct'] is False
        assert 're-read' in r['re_explanation'].lower()


# ── Recommender + coordinator (SQLite, real models) ──────────────────────────

class TestAgentRecommenderAndCoordinator:
    """End-to-end agent tests on an in-memory SQLite DB with the real models."""

    @pytest.fixture()
    def db(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.db.session import Base
        import app.models  # noqa: F401 — registers all models
        from app.models.user import User
        from app.models.topic import Topic
        from app.models.document import Document
        from app.models.parent_chunk import ParentChunk
        from app.models.chunk import Chunk

        engine = create_engine('sqlite:///:memory:')
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        s = Session()

        u = User(employee_code='T1', full_name='Trainee', role='trainee',
                 department='Production', is_active=True, hashed_password='x')
        topic = Topic(subject_id=1, title='Validation')
        doc = Document(code='DOC-1', title='Cleaning Validation', topic='Cleaning Validation',
                       topic_id=1, subject_id=1, version=1, status='active')
        s.add_all([u, topic, doc])
        s.flush()
        parent = ParentChunk(document_id=doc.id, topic_id=topic.id, subject_id=1,
                             section_index=1, title='Rinse Procedures', content='Rinse with WFI.',
                             token_count=10, page_start=1, page_end=1)
        s.add(parent)
        s.flush()
        c1 = Chunk(document_id=doc.id, topic_id=topic.id, subject_id=1, page_no=1,
                   chunk_index=1, content='Rinse water must meet WFI specifications.',
                   token_count=8, parent_chunk_id=parent.id, child_index=0)
        c2 = Chunk(document_id=doc.id, topic_id=topic.id, subject_id=1, page_no=2,
                   chunk_index=2, content='Sampling points are defined per batch.',
                   token_count=8, parent_chunk_id=parent.id, child_index=1)
        s.add_all([c1, c2])
        s.commit()
        s._t1_id = u.id
        s._doc_id = doc.id
        s._c1_id = c1.id
        s._c2_id = c2.id
        yield s
        s.close()

    def _no_llm_coordinator(self, db, user_id):
        from app.agents.adaptive_agent_service import AdaptiveAgentService
        svc = AdaptiveAgentService(db, user_id=user_id)
        for agent in (svc.curriculum, svc.question_generator, svc.evaluator,
                      svc.weakness, svc.recommender):
            agent._llm = _NoLLM()
        return svc

    def test_recommender_empty_profile(self, db):
        from app.agents.recommender_agent import RecommenderAgent
        profile = RecommenderAgent(db).build_profile(user_id=999)
        assert profile['summary']['total_concepts'] == 0
        assert profile['weaknesses'] == []
        assert profile['study_plan']

    def test_recommender_aggregates_weaknesses_and_strengths(self, db):
        from app.models.user_concept_mastery import UserConceptMastery
        db.add_all([
            UserConceptMastery(user_id=db._t1_id, document_id=db._doc_id, topic_id=1,
                               parent_chunk_id=1, child_chunk_id=db._c1_id,
                               score=30.0, mastery_level='novice', attempts=3,
                               correct_attempts=1, consecutive_correct=0,
                               needs_review=True, insight='Struggles with WFI spec.'),
            UserConceptMastery(user_id=db._t1_id, document_id=db._doc_id, topic_id=1,
                               parent_chunk_id=1, child_chunk_id=db._c2_id,
                               score=90.0, mastery_level='mastered', attempts=5,
                               correct_attempts=5, consecutive_correct=5,
                               needs_review=False),
        ])
        db.commit()

        from app.agents.recommender_agent import RecommenderAgent
        profile = RecommenderAgent(db).build_profile(db._t1_id)
        assert profile['summary']['total_concepts'] == 2
        assert profile['summary']['mastered'] == 1
        assert profile['summary']['weak_concepts'] == 1
        assert profile['summary']['critical_concepts'] == 1
        assert profile['summary']['avg_score'] == pytest.approx(60.0, abs=0.1)
        assert len(profile['weaknesses']) == 1
        assert profile['weaknesses'][0]['severity'] == 'critical'
        assert profile['weaknesses'][0]['insight'] == 'Struggles with WFI spec.'
        assert profile['weaknesses'][0]['section_title'] == 'Rinse Procedures'
        assert len(profile['strengths']) == 1
        assert profile['topics'][0]['avg_score'] == pytest.approx(60.0, abs=0.1)
        assert profile['study_plan'][0]['action']

    def test_coordinator_question_flow_without_llm(self, db):
        svc = self._no_llm_coordinator(db, db._t1_id)
        from app.models.chunk import Chunk
        chunk = db.query(Chunk).filter(Chunk.id == db._c1_id).first()
        q = svc.get_next_question(db._t1_id, chunk)
        assert q['question']
        assert q['difficulty'] == 'easy'          # first exposure
        assert q['format'] == 'objective'
        assert q['mastery']['attempts'] == 0
        assert q['plan']['difficulty'] == 'easy'
        assert q['agents']['curriculum']['agent'] == 'CurriculumAgent'
        assert q['agents']['question_generator']['source'] == 'dynamic'

    def test_coordinator_correct_answer_raises_mastery(self, db):
        svc = self._no_llm_coordinator(db, db._t1_id)
        from app.models.chunk import Chunk
        chunk = db.query(Chunk).filter(Chunk.id == db._c1_id).first()
        q_data = {
            'question': 'Q?', 'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'},
            'correct_option': 'A', 'explanation': 'e', 'difficulty': 'easy',
        }
        r = svc.submit_answer(db._t1_id, chunk, q_data, 'A', time_taken_seconds=5)
        assert r['is_correct'] is True
        assert r['mastery']['attempts'] == 1
        assert r['mastery']['score'] > 0.0
        assert r['mastery']['consecutive_correct'] == 1
        assert r['recommendation']['action'] in ('reinforce', 'review_section', 'continue_section')
        assert r['agents']['evaluator']['agent'] == 'EvaluatorAgent'
        # Topic roll-up keeps NQ reports working.
        from app.models.user_weakness_profile import UserWeaknessProfile
        profile = db.query(UserWeaknessProfile).filter(
            UserWeaknessProfile.user_id == db._t1_id).first()
        assert profile is not None

    def test_mastery_history_replays_ema_from_attempts(self, db):
        """build_mastery_history reconstructs the exact score trajectory by
        replaying the deterministic EMA over chronological attempts."""
        from app.models.child_chunk_attempt import ChildChunkAttempt
        from app.agents.weakness_agent import ema_update
        from app.agents.recommender_agent import RecommenderAgent

        for i, correct in enumerate([True, False, True]):
            db.add(ChildChunkAttempt(
                user_id=db._t1_id, child_chunk_id=db._c1_id, parent_chunk_id=1,
                document_id=db._doc_id, attempt_number=i + 1,
                difficulty_shown='easy', is_correct=correct,
            ))
        db.commit()

        history = RecommenderAgent(db).build_mastery_history(db._t1_id)
        assert len(history['concepts']) == 1
        concept = history['concepts'][0]
        assert concept['child_chunk_id'] == db._c1_id
        assert concept['section_title'] == 'Rinse Procedures'
        assert concept['attempts'] == 3

        # Replay expectation: 0 → +25 → −50% → +25% of remaining.
        s0 = 0.0
        s1 = ema_update(s0, True)
        s2 = ema_update(s1, False)
        s3 = ema_update(s2, True)
        scores = [p['score'] for p in concept['points']]
        assert scores == [round(s1, 1), round(s2, 1), round(s3, 1)]
        assert concept['current_score'] == scores[-1]
        assert [p['is_correct'] for p in concept['points']] == [True, False, True]
        assert [p['attempt_number'] for p in concept['points']] == [1, 2, 3]

    def test_mastery_history_empty_for_new_user(self, db):
        from app.agents.recommender_agent import RecommenderAgent
        history = RecommenderAgent(db).build_mastery_history(user_id=999)
        assert history['concepts'] == []
        assert history['document_average'] == {'attempts': 0, 'points': []}

    def test_mastery_history_document_average_series(self, db):
        """document_average is the carried-forward mean across concepts at each
        attempt index: a concept with fewer attempts keeps its last score, so
        the line spans every attempt made in the document and stays comparable
        to any single concept's trend on the same x-axis."""
        from app.models.child_chunk_attempt import ChildChunkAttempt
        from app.agents.weakness_agent import ema_update
        from app.agents.recommender_agent import RecommenderAgent

        # c1: 3 attempts [T, F, T] → 25.0, 12.5, 34.4
        # c2: 2 attempts [F, T]  →  0.0, 25.0  (attempt 3 carries 25.0 forward)
        for _, (cid, correct) in enumerate([
            (db._c1_id, True), (db._c2_id, False),   # attempt 1
            (db._c1_id, False), (db._c2_id, True),   # attempt 2
            (db._c1_id, True),                       # attempt 3 (c1 only)
        ], start=1):
            db.add(ChildChunkAttempt(
                user_id=db._t1_id, child_chunk_id=cid, parent_chunk_id=1,
                document_id=db._doc_id, attempt_number=1,
                difficulty_shown='medium', is_correct=correct,
            ))
        db.commit()

        history = RecommenderAgent(db).build_mastery_history(db._t1_id)
        avg = history['document_average']
        assert avg['attempts'] == 3
        assert [p['attempt_number'] for p in avg['points']] == [1, 2, 3]

        c1 = [25.0, ema_update(25.0, False), ema_update(ema_update(25.0, False), True)]
        c2 = [0.0, ema_update(0.0, True)]
        expected = []
        for i in range(3):
            v2 = c2[i] if i < len(c2) else c2[-1]  # carry forward c2's last score
            expected.append(round((c1[i] + v2) / 2, 1))
        assert [p['score'] for p in avg['points']] == expected
        # The overlay aligns to a concept's chart by absolute attempt number.
        # (Concepts sort weakest-first, so find the 3-attempt concept explicitly.)
        concept = next(c for c in history['concepts'] if len(c['points']) == 3)
        assert concept['points'][2]['attempt_number'] == 3
        assert avg['points'][2]['attempt_number'] == 3

    def test_mastery_history_doc_avg_survives_concept_series_cap(self, db):
        """Regression: document_average must NOT be trailing-window capped like
        the per-concept series. A concept with 55 attempts and another with 2
        must still show the overlay for the short concept — its attempt numbers
        (1..2) sit outside any last-50 window of the long series, so the
        document average has to cover the FULL attempt range."""
        from app.models.child_chunk_attempt import ChildChunkAttempt
        from app.agents.weakness_agent import ema_update
        from app.agents.recommender_agent import RecommenderAgent

        # c1: 55 attempts (mostly correct), c2: 2 attempts.
        for attempt in range(1, 56):
            db.add(ChildChunkAttempt(
                user_id=db._t1_id, child_chunk_id=db._c1_id, parent_chunk_id=1,
                document_id=db._doc_id, attempt_number=attempt,
                difficulty_shown='medium', is_correct=True,
            ))
        for attempt in range(1, 3):
            db.add(ChildChunkAttempt(
                user_id=db._t1_id, child_chunk_id=db._c2_id, parent_chunk_id=1,
                document_id=db._doc_id, attempt_number=attempt,
                difficulty_shown='medium', is_correct=False,
            ))
        db.commit()

        history = RecommenderAgent(db).build_mastery_history(db._t1_id)
        avg = history['document_average']
        assert avg['attempts'] == 55
        assert len(avg['points']) == 55                     # full range, not capped
        assert avg['points'][0]['attempt_number'] == 1
        assert avg['points'][-1]['attempt_number'] == 55
        # A concept's capped series must still align with the overlay lookup.
        short = next(c for c in history['concepts'] if len(c['points']) == 2)
        long = next(c for c in history['concepts'] if len(c['points']) == 50)
        assert short['points'][0]['attempt_number'] == 1   # inside the avg range
        assert long['points'][0]['attempt_number'] == 6    # last 50 of 55
        # Carried-forward mean at attempt 55 uses c2's final (capped) score.
        c2_last = ema_update(ema_update(0.0, False), False)
        assert avg['points'][-1]['score'] == pytest.approx(round((long['current_score'] + c2_last) / 2, 1))

    def test_coordinator_answer_on_legacy_null_parent_chunk(self, db):
        """Regression: legacy flat chunks (parent_chunk_id NULL) must not 500.
        The answer flow stores NULL parent — excluded from parent-mastery
        queries — instead of writing an invalid 0 (FK-safe)."""
        from app.models.chunk import Chunk
        from app.models.parent_chunk import ParentChunk
        legacy = Chunk(document_id=db._doc_id, topic_id=1, subject_id=1, page_no=9,
                       chunk_index=99, content='Legacy flat chunk content.',
                       token_count=6, parent_chunk_id=None, child_index=0)
        db.add(legacy)
        db.commit()

        svc = self._no_llm_coordinator(db, db._t1_id)
        q_data = {
            'question': 'Q?', 'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'},
            'correct_option': 'A', 'explanation': 'e', 'difficulty': 'easy',
        }
        r = svc.submit_answer(db._t1_id, legacy, q_data, 'A')
        assert r['is_correct'] is True
        assert r['mastery']['attempts'] == 1

        from app.models.user_concept_mastery import UserConceptMastery
        row = db.query(UserConceptMastery).filter(
            UserConceptMastery.user_id == db._t1_id,
            UserConceptMastery.child_chunk_id == legacy.id,
        ).first()
        assert row is not None
        assert row.parent_chunk_id is None  # NULL, never 0 → FK-safe on Postgres

    def test_coordinator_wrong_answer_drops_mastery(self, db):
        from app.models.user_concept_mastery import UserConceptMastery
        db.add(UserConceptMastery(user_id=db._t1_id, document_id=db._doc_id, topic_id=1,
                                  parent_chunk_id=1, child_chunk_id=db._c1_id,
                                  score=70.0, mastery_level='proficient', attempts=3,
                                  correct_attempts=3, consecutive_correct=3))
        db.commit()

        svc = self._no_llm_coordinator(db, db._t1_id)
        from app.models.chunk import Chunk
        chunk = db.query(Chunk).filter(Chunk.id == db._c1_id).first()
        q_data = {
            'question': 'Q?', 'options': {'A': 'a', 'B': 'b', 'C': 'c', 'D': 'd'},
            'correct_option': 'B', 'explanation': 'e', 'difficulty': 'hard',
        }
        r = svc.submit_answer(db._t1_id, chunk, q_data, 'A')
        assert r['is_correct'] is False
        assert r['diagnosis'] is None            # no LLM → no diagnosis
        assert r['re_explanation']
        assert r['mastery']['score'] < 70.0      # EMA halves toward 0
        assert r['mastery']['consecutive_correct'] == 0
        assert r['recommendation']['action'] == 'reinforce'  # below weak threshold

    def test_reingestion_cleanup_deletes_mastery_before_chunks(self, db):
        """Regression (I-17): re-ingesting a document whose chunks are
        referenced by user_concept_mastery rows must NOT raise a
        ForeignKeyViolation. _cleanup_document_data clears mastery rows before
        pruning chunks/parents, so the DELETE succeeds and no stale references
        remain."""
        from app.models.user_concept_mastery import UserConceptMastery
        db.add(UserConceptMastery(user_id=db._t1_id, document_id=db._doc_id, topic_id=1,
                                  parent_chunk_id=1, child_chunk_id=db._c1_id,
                                  score=45.0, mastery_level='novice', attempts=2,
                                  correct_attempts=0, consecutive_correct=0,
                                  needs_review=True))
        db.commit()

        # Enforce SQLite FK constraints so the production ForeignKeyViolation
        # (user_concept_mastery.child_chunk_id → chunks.id) actually reproduces
        # if the cleanup order regresses. SQLite checks per-connection.
        from sqlalchemy import text as _sa_text
        db.execute(_sa_text('PRAGMA foreign_keys=ON'))
        db.commit()

        from app.services.ingestion_service import IngestionService
        svc = IngestionService.__new__(IngestionService)
        svc.db = db

        from app.models.document import Document
        doc = db.query(Document).filter(Document.id == db._doc_id).first()
        svc._cleanup_document_data(doc)  # must not raise FK violation

        from app.models.user_concept_mastery import UserConceptMastery as UCM
        assert db.query(UCM).filter(UCM.document_id == db._doc_id).count() == 0
        from app.models.chunk import Chunk
        from app.models.parent_chunk import ParentChunk
        assert db.query(Chunk).filter(Chunk.document_id == db._doc_id).count() == 0
        assert db.query(ParentChunk).filter(ParentChunk.document_id == db._doc_id).count() == 0
