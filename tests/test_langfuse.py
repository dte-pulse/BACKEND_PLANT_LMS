"""
Langfuse observability wrapper tests.

The wrapper must be a graceful no-op when Langfuse is unconfigured (the app
keeps working exactly as before — matching the codebase's fallback style), and
must route observations to the real SDK when configured.
"""
import contextlib

import pytest

import app.clients.langfuse_client as lf


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the module-level lazy singleton between tests."""
    lf._langfuse = None
    lf._initialized = False
    yield
    lf._langfuse = None
    lf._initialized = False


def _disable(monkeypatch):
    monkeypatch.setattr(lf.settings, 'langfuse_public_key', '')
    monkeypatch.setattr(lf.settings, 'langfuse_secret_key', '')
    monkeypatch.setattr(lf.settings, 'langfuse_enabled', True)


def _enable(monkeypatch):
    monkeypatch.setattr(lf.settings, 'langfuse_public_key', 'pk-lf-test')
    monkeypatch.setattr(lf.settings, 'langfuse_secret_key', 'sk-lf-test')
    monkeypatch.setattr(lf.settings, 'langfuse_base_url', 'https://example.langfuse.com')
    monkeypatch.setattr(lf.settings, 'langfuse_enabled', True)


# ── No-op behaviour when unconfigured ────────────────────────────────────────

class TestUnconfiguredNoop:
    def test_configured_false_without_keys(self, monkeypatch):
        _disable(monkeypatch)
        assert lf.langfuse_configured() is False

    def test_get_langfuse_returns_none_without_keys(self, monkeypatch):
        _disable(monkeypatch)
        assert lf.get_langfuse() is None

    def test_observation_noops_without_keys(self, monkeypatch):
        _disable(monkeypatch)
        with lf.langfuse_observation(name='x', as_type='generation') as obs:
            obs.update(output='y', usage_details={'input': 1})
            obs.score(name='s', value=0.5)
            obs.score_trace(name='t', value=1.0)
        assert isinstance(obs, lf.NoopObservation)

    def test_observation_block_still_runs_without_keys(self, monkeypatch):
        """The instrumented code inside the with-block must execute normally."""
        _disable(monkeypatch)
        ran = []
        with lf.langfuse_observation(name='x'):
            ran.append(1)
        assert ran == [1]

    def test_score_helpers_noop_without_keys(self, monkeypatch):
        _disable(monkeypatch)
        lf.score_current_trace('qa-groundedness', 0.9)  # must not raise
        lf.score_trace(lf._NOOP, 'qa-groundedness', 0.9)
        lf.score_observation(lf._NOOP, 'mcq-quality', 0.8)

    def test_flush_and_shutdown_noop_without_keys(self, monkeypatch):
        _disable(monkeypatch)
        lf.flush_langfuse()     # must not raise
        lf.shutdown_langfuse()


# ── Sampling ─────────────────────────────────────────────────────────────────

class TestSampling:
    def test_full_rate_always_samples(self):
        assert lf.should_sample(1.0) is True
        assert lf.should_sample(0.999) in (True, False)

    def test_zero_rate_never_samples(self):
        assert lf.should_sample(0.0) is False
        assert lf.should_sample(None) is False

    def test_sampled_out_observation_is_noop(self, monkeypatch):
        _enable(monkeypatch)
        monkeypatch.setattr(lf, 'should_sample', lambda rate: False)
        with lf.langfuse_observation(name='x', sample_rate=0.5) as obs:
            assert isinstance(obs, lf.NoopObservation)

    def test_sampled_out_root_suppresses_children(self, monkeypatch):
        """Regression: a sampled-out root must NOT orphan its children — they
        would otherwise become their own root traces (empty OTel context)."""
        _enable(monkeypatch)
        fake = _FakeLangfuse()
        monkeypatch.setattr(lf, 'get_langfuse', lambda: fake)
        monkeypatch.setattr(lf, 'should_sample', lambda rate: False)

        with lf.langfuse_observation(name='ingest-root', sample_rate=0.1):
            with lf.langfuse_observation(name='child-embed') as child:
                assert isinstance(child, lf.NoopObservation)
            with lf.langfuse_observation(name='child-gen') as child2:
                assert isinstance(child2, lf.NoopObservation)

        assert fake.started == []  # nothing reached the SDK

    def test_sampled_in_root_allows_children(self, monkeypatch):
        _enable(monkeypatch)
        fake = _FakeLangfuse()
        monkeypatch.setattr(lf, 'get_langfuse', lambda: fake)
        monkeypatch.setattr(lf, 'should_sample', lambda rate: True)

        with lf.langfuse_observation(name='root', sample_rate=0.1):
            with lf.langfuse_observation(name='child') as child:
                child.update(output='ok')

        assert len(fake.started) == 2
        assert fake.started[0]['name'] == 'root'
        assert fake.started[1]['name'] == 'child'


# ── Observation plumbing with a fake client ─────────────────────────────────

class _FakeObs:
    def __init__(self):
        self.updates = []
        self.scores = []
        self.trace_scores = []

    def update(self, **kw):
        self.updates.append(kw)

    def score(self, **kw):
        self.scores.append(kw)

    def score_trace(self, **kw):
        self.trace_scores.append(kw)


class _FakeUsage:
    """Mimics google.genai UsageMetadata (prompt/candidates/cached/thoughts token counts)."""
    def __init__(self, prompt=0, candidates=0, cached=0, thoughts=0):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.cached_content_token_count = cached
        self.thoughts_token_count = thoughts


class _FakeGenResponse:
    """Mimics a Gemini generate_content response."""
    def __init__(self, usage=None, text='some answer text'):
        self.usage_metadata = usage
        self.text = text


class _FakeLangfuse:
    def __init__(self, current_obs_id=None):
        self.started = []
        self.obs = _FakeObs()
        self._current_obs_id = current_obs_id

    def get_current_observation_id(self):
        return self._current_obs_id

    @contextlib.contextmanager
    def start_as_current_observation(self, **kw):
        self.started.append(kw)
        yield self.obs


class TestObservationPlumbing:
    def test_observation_forwards_args_and_yields_real_obs(self, monkeypatch):
        _enable(monkeypatch)
        fake = _FakeLangfuse()
        monkeypatch.setattr(lf, 'get_langfuse', lambda: fake)

        with lf.langfuse_observation(
            name='my-generation', as_type='generation', model='gemini-2.5-flash',
            input_data={'prompt': 'hi'}, metadata={'operation': 'qa'},
        ) as obs:
            obs.update(output='hello', usage_details={'input': 1, 'output': 2})
            obs.score_trace(name='correctness', value=1.0, data_type='BOOLEAN')

        assert fake.started[0]['name'] == 'my-generation'
        assert fake.started[0]['as_type'] == 'generation'
        assert fake.started[0]['model'] == 'gemini-2.5-flash'
        assert fake.started[0]['input'] == {'prompt': 'hi'}
        assert fake.started[0]['metadata'] == {'operation': 'qa'}
        assert fake.obs.updates == [{'output': 'hello', 'usage_details': {'input': 1, 'output': 2}}]
        assert fake.obs.trace_scores == [{'name': 'correctness', 'value': 1.0, 'data_type': 'BOOLEAN'}]

    def test_usage_details_uses_real_gemini_metadata(self):
        """Langfuse usage comes from Gemini's REAL usage_metadata: canonical
        keys ('input'/'output'/'input_cached_tokens' match the predefined
        gemini-2.5-flash price definition verbatim) with the cached portion
        split out of input so the buckets stay mutually exclusive."""
        from app.clients.llm_client import LLMClient
        resp = _FakeGenResponse(usage=_FakeUsage(prompt=1000, candidates=200, cached=300))
        details = LLMClient._usage_details(resp, 'prompt text', 'answer text')
        assert details == {'input': 700, 'output': 200, 'input_cached_tokens': 300}

    def test_usage_details_falls_back_to_estimates_without_metadata(self):
        """No usage_metadata (mock/fallback paths) → word-count estimates, with
        the canonical keys preserved so cost still computes when it can."""
        from app.clients.llm_client import LLMClient
        details = LLMClient._usage_details(_FakeGenResponse(usage=None), 'hello world prompt', 'short answer')
        assert set(details) == {'input', 'output', 'input_cached_tokens'}
        assert details['input'] > 0
        assert details['output'] > 0
        assert details['input_cached_tokens'] == 0

    def test_usage_from_response_parses_metadata(self):
        """usage_from_response returns (input, output, cached) from metadata and
        estimates when metadata is absent."""
        from app.utils.tokenizer import usage_from_response
        in_tok, out_tok, cached = usage_from_response(
            _FakeGenResponse(usage=_FakeUsage(prompt=50, candidates=10, cached=5)), 'p', 't'
        )
        assert (in_tok, out_tok, cached) == (50, 10, 5)
        # Thinking tokens are billed at the output rate → folded into output
        in_tok, out_tok, cached = usage_from_response(
            _FakeGenResponse(usage=_FakeUsage(prompt=3, candidates=2, cached=0, thoughts=16)), 'p', 't'
        )
        assert (in_tok, out_tok, cached) == (3, 18, 0)
        in_tok, out_tok, cached = usage_from_response(_FakeGenResponse(usage=None), 'hello world', 'hi')
        assert in_tok > 0 and out_tok > 0 and cached == 0

    def test_score_helpers_forward_to_observation(self, monkeypatch):
        _enable(monkeypatch)
        fake = _FakeLangfuse()
        monkeypatch.setattr(lf, 'get_langfuse', lambda: fake)
        obs = _FakeObs()
        lf.score_trace(obs, 'q', 0.9, 'NUMERIC', 'good')
        lf.score_observation(obs, 'm', 0.8, 'NUMERIC', 'ok')
        assert obs.trace_scores == [{'name': 'q', 'value': 0.9, 'data_type': 'NUMERIC', 'comment': 'good'}]
        assert obs.scores == [{'name': 'm', 'value': 0.8, 'data_type': 'NUMERIC', 'comment': 'ok'}]

    def test_singleton_initialised_once(self, monkeypatch):
        _enable(monkeypatch)
        import langfuse as _langfuse_pkg
        monkeypatch.setattr(_langfuse_pkg, 'Langfuse', lambda **kw: object())
        first = lf.get_langfuse()
        second = lf.get_langfuse()
        assert first is second


# ── Trace-name propagation + user-id coercion ────────────────────────────────

@pytest.fixture
def _capture_propagation(monkeypatch):
    """Replace langfuse.propagate_attributes with a recording context manager
    that appends each call's kwargs (so nested calls are distinguishable)."""
    import langfuse as _langfuse_pkg
    calls = []

    @contextlib.contextmanager
    def _recorder(**kw):
        calls.append(kw)
        yield

    monkeypatch.setattr(_langfuse_pkg, 'propagate_attributes', _recorder)
    return calls


class TestTraceNamePropagation:
    def test_root_propagates_trace_name(self, monkeypatch, _capture_propagation):
        """The outermost observation of a trace propagates its own name as
        trace_name so children resolve in the metrics traceName dimension."""
        _enable(monkeypatch)
        fake = _FakeLangfuse()
        monkeypatch.setattr(lf, 'get_langfuse', lambda: fake)

        with lf.langfuse_observation(name='rag-qa', as_type='chain', user_id=3):
            pass

        assert _capture_propagation[0].get('trace_name') == 'rag-qa'

    def test_child_does_not_override_trace_name(self, monkeypatch, _capture_propagation):
        """A nested observation must NOT propagate its own name as trace_name
        (it would clobber the parent's propagated trace name)."""
        _enable(monkeypatch)
        fake = _FakeLangfuse()
        monkeypatch.setattr(lf, 'get_langfuse', lambda: fake)

        with lf.langfuse_observation(name='rag-qa', as_type='chain', user_id=3):
            with lf.langfuse_observation(name='rag-answer-generation', as_type='generation', user_id=3):
                pass

        # Root propagates the chain name; the child must NOT pass its own.
        assert _capture_propagation[0].get('trace_name') == 'rag-qa'
        assert 'trace_name' not in _capture_propagation[1]

    def test_user_id_and_session_id_coerced_to_string(self, monkeypatch, _capture_propagation):
        """The SDK drops non-string user_id/session_id; the wrapper must coerce
        the app's int primary keys to strings for per-user attribution."""
        _enable(monkeypatch)
        fake = _FakeLangfuse()
        monkeypatch.setattr(lf, 'get_langfuse', lambda: fake)

        with lf.langfuse_observation(name='rag-qa', as_type='chain', user_id=3, session_id=7):
            pass

        assert _capture_propagation[0]['user_id'] == '3'
        assert _capture_propagation[0]['session_id'] == '7'

    def test_body_exception_propagates_unchanged(self, monkeypatch, _capture_propagation):
        """Regression (I-17): an exception raised INSIDE an instrumented block
        must propagate to the caller exactly as-is. The old
        ``except Exception: yield`` in ``_propagate_attrs`` caught body errors
        and yielded again after the throw, which made contextlib raise
        ``RuntimeError: generator didn't stop after throw()`` and masked the
        real failure (e.g. the FK violation during document re-ingestion)."""
        _enable(monkeypatch)
        fake = _FakeLangfuse()
        monkeypatch.setattr(lf, 'get_langfuse', lambda: fake)

        class _Boom(Exception):
            pass

        with pytest.raises(_Boom, match='fk-violation'):
            with lf.langfuse_observation(name='ingest-document', as_type='span', user_id=3):
                raise _Boom('fk-violation')

        # Same guarantee with the no-propagation path (no user/tags/trace attrs)
        with pytest.raises(_Boom, match='plain-error'):
            with lf.langfuse_observation(name='ingest-document', as_type='span'):
                raise _Boom('plain-error')


# ── LLM client must be bounded (no unbounded Gemini calls) ───────────────────

class TestLlmClientTimeout:
    def test_gemini_client_constructed_with_request_timeout(self, monkeypatch):
        """The adaptive answer path must never hang on a slow Gemini call — the
        genai client is built with a 30s request timeout so the frontend's
        (raised) axios timeout is always enough and agents can fall back."""
        _enable(monkeypatch)
        monkeypatch.setattr(lf.settings, 'gemini_api_key', 'test-key')
        captured = {}

        class _FakeGenaiClient:
            def __init__(self, **kwargs):
                captured['kwargs'] = kwargs

        import google.genai as genai_pkg
        monkeypatch.setattr(genai_pkg, 'Client', _FakeGenaiClient)

        from app.clients.llm_client import LLMClient
        client = LLMClient()
        assert client.model == 'gemini-2.5-flash'
        http_options = captured['kwargs'].get('http_options')
        assert http_options is not None
        assert http_options.timeout == 30_000


# ── Instrumented flows keep working when unconfigured ────────────────────────

class TestInstrumentedFlowsNoop:
    def test_instrumented_llm_client_works_unconfigured(self, monkeypatch):
        """LLMClient must behave identically with Langfuse disabled (mock mode)."""
        _disable(monkeypatch)
        monkeypatch.setattr(lf.settings, 'gemini_api_key', '')  # no Gemini → mock path
        from app.clients.llm_client import LLMClient
        client = LLMClient()
        assert client.model is None
        assert client.generate_text('hello') is None
        assert client.generate_json('{}') is None
        assert client.generate_section_title('Some content here.') == 'Section'

    def test_rag_generation_path_imports_cleanly(self, monkeypatch):
        """The RAG + QA endpoints import fine with Langfuse code in place."""
        _disable(monkeypatch)
        import app.api.v1.endpoints.learning_session as ls_mod
        import app.services.rag_service as rag_mod
        import app.evals.judges as judges_mod
        assert callable(ls_mod.ask_question)
        assert callable(ls_mod._ask_question_impl)
        assert callable(rag_mod.RagService.retrieve_chunks_hybrid)
        assert callable(judges_mod.judge_qa_groundedness)
