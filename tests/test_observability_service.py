"""
ObservabilityService — Langfuse Metrics API v2 fan-out + rate-limit tests.

The dashboard is the heaviest Langfuse consumer in the app: one load used to
fire 5+ Metrics API requests (Hobby tier allows only ~100/day), which is what
exhausted the quota and produced the 429. These tests pin the new behaviour:

- trace count is derived from the paginated traces fetch, so the dedicated
  root-count metrics query is gone (4 metrics requests per load, not 5),
- 429s are retried once when Retry-After is short, and otherwise surface a
  friendly error instead of a raw HTTP trace.
"""
import json

import pytest

import app.services.observability_service as obs_mod


@pytest.fixture
def configured(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, 'langfuse_public_key', 'pk-lf-test')
    monkeypatch.setattr(settings, 'langfuse_secret_key', 'sk-lf-test')
    monkeypatch.setattr(settings, 'langfuse_base_url', 'https://example.langfuse.com')
    monkeypatch.setattr(settings, 'langfuse_enabled', True)


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None):
        self.status_code = status_code
        self._json = json_body
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f'Client error {self.status_code}', request=None, response=self
            )


class _FakeLangfuseAPI:
    """Scripted httpx.get replacement keyed on the metrics ``view`` / traces path.

    Each metrics view returns the response bound to it, so the test can assert
    exactly which queries were fired (and how many), while 429s can be
    injected per-view to exercise the retry path.
    """

    def __init__(self, totals=None, trend=None, numeric=None, boolean=None, traces=None):
        self.responses = {
            'observations': _FakeResponse(json_body={'data': totals or []}),
            'trend': _FakeResponse(json_body={'data': trend or []}),
            'scores-numeric': _FakeResponse(json_body={'data': numeric or []}),
            'scores-boolean': _FakeResponse(json_body={'data': boolean or []}),
            'traces': _FakeResponse(json_body=traces or {'data': [], 'meta': {'totalPages': 1}}),
        }
        self.next_status = {}   # view/kind → 429 (consumed once)
        self.retry_after = {}   # view/kind → 'seconds'
        self.views = []         # metrics view names, in request order
        self.metrics_calls = 0  # total hits on /api/public/v2/metrics

    def __call__(self, url, params=None, **kwargs):
        params = params or {}
        if url.endswith('/api/public/traces'):
            return self._respond('traces')
        self.metrics_calls += 1
        query = json.loads(params['query'])
        # The trend query shares the 'observations' view — tell it apart by its
        # timeDimension so the fake returns the right rows (and the test can
        # verify the root-count query is truly gone).
        kind = 'trend' if query.get('timeDimension') else query.get('view')
        self.views.append(kind)
        return self._respond(kind)

    def _respond(self, kind):
        if kind in self.next_status and self.next_status[kind]:
            status = self.next_status.pop(kind)
            if status == 429:
                return _FakeResponse(429, {'detail': 'rate limited'},
                                     {'Retry-After': self.retry_after.get(kind, '0')})
            return _FakeResponse(status, {'detail': 'oops'})
        return self.responses[kind]


def _trace_row(trace_id, name, cost=0.05, latency=0.3):
    return {
        'id': trace_id, 'name': name, 'timestamp': '2026-08-12T00:00:00Z',
        'latency': latency, 'totalCost': cost, 'level': 'DEFAULT',
        'htmlPath': f'/trace/{trace_id}',
    }


# ── Happy path: metrics fan-out is minimal, traces derived, no root query ───

class TestHappyPath:
    def test_dashboard_derives_traces_and_skips_root_count(self, monkeypatch, configured):
        totals = [{'count_count': 42, 'sum_totalCost': 1.5, 'avg_latency': 200.0, 'p95_latency': 500.0}]
        trend = [{'time_dimension': '2026-08-11T00:00:00Z', 'count_count': 10,
                  'sum_totalCost': 0.4, 'avg_latency': 150.0, 'p95_latency': 300.0}]
        numeric = [{'name': 'qa-groundedness', 'avg_value': 0.92, 'count_count': 4}]
        boolean = [{'name': 'answer-correctness', 'avg_value': 0.8, 'count_count': 6}]
        traces = {'data': [_trace_row('t1', 'rag-qa'), _trace_row('t2', 'adaptive-question')],
                  'meta': {'totalPages': 1}}
        api = _FakeLangfuseAPI(totals=totals, trend=trend, numeric=numeric, boolean=boolean, traces=traces)
        monkeypatch.setattr(obs_mod.httpx, 'get', api)

        data = obs_mod.ObservabilityService().get_dashboard(days=7)

        assert data['configured'] is True
        assert 'error' not in data

        # Exactly 4 metrics requests — totals, trend, scores-numeric, scores-boolean.
        # The old root-count query would have shown up as a second
        # 'observations' view, so seeing exactly one proves it's gone.
        assert api.metrics_calls == 4
        assert api.views == ['observations', 'trend', 'scores-numeric', 'scores-boolean']

        assert data['summary']['traces'] == 2           # from trace rows
        assert data['summary']['operations'] == 42
        assert data['summary']['cost_usd'] == pytest.approx(1.5)
        assert data['summary']['avg_latency_ms'] == pytest.approx(200.0)
        assert data['summary']['p95_latency_ms'] == pytest.approx(500.0)

        assert [f['feature'] for f in data['by_feature']] == ['rag-qa', 'adaptive-question']
        assert len(data['recent_traces']) == 2
        assert data['trend'] == [{'date': '2026-08-11', 'operations': 10, 'cost_usd': 0.4,
                                  'avg_latency_ms': 150.0, 'p95_latency_ms': 300.0}]
        assert {e['name']: (e['type'], e['count']) for e in data['evals']} == {
            'qa-groundedness': ('numeric', 4),
            'answer-correctness': ('boolean', 6),
        }

    def test_pagination_stops_at_total_pages(self, monkeypatch, configured):
        page1 = {'data': [_trace_row('t1', 'rag-qa')], 'meta': {'totalPages': 2}}
        page2 = {'data': [_trace_row('t2', 'ingest-document')], 'meta': {'totalPages': 2}}
        api = _FakeLangfuseAPI(traces=page1)
        # Second traces call returns page 2 — reach the last page and stop.
        real = api

        def getter(url, params=None, **kwargs):
            params = params or {}
            if url.endswith('/api/public/traces'):
                if params.get('page') == 1:
                    return _FakeResponse(json_body=page1)
                return _FakeResponse(json_body=page2)
            return real(url, params, **kwargs)

        monkeypatch.setattr(obs_mod.httpx, 'get', getter)
        data = obs_mod.ObservabilityService().get_dashboard(days=7)

        assert data['summary']['traces'] == 2
        assert data['configured'] is True and 'error' not in data


# ── 429 rate limiting: bounded retry + friendly error ───────────────────────

class TestRateLimit:
    def test_429_with_long_retry_after_surfaces_friendly_error(self, monkeypatch, configured):
        api = _FakeLangfuseAPI()
        api.next_status['observations'] = 429
        api.retry_after['observations'] = '100'   # longer than the 30s cap → no retry
        monkeypatch.setattr(obs_mod.httpx, 'get', api)

        data = obs_mod.ObservabilityService().get_dashboard(days=7)

        assert data['configured'] is True
        assert 'error' in data
        assert '429' in data['error'] and 'rate limit' in data['error'].lower()
        assert api.metrics_calls == 1             # no retry — quota clearly gone

    def test_429_retries_once_with_short_retry_after(self, monkeypatch, configured):
        totals = [{'count_count': 7, 'sum_totalCost': 0.1, 'avg_latency': 100.0, 'p95_latency': 200.0}]
        api = _FakeLangfuseAPI(totals=totals)
        api.next_status['observations'] = 429     # consumed on the FIRST metrics call
        api.retry_after['observations'] = '0'     # seconds — avoids sleeping in tests
        monkeypatch.setattr(obs_mod.httpx, 'get', api)

        data = obs_mod.ObservabilityService().get_dashboard(days=7)

        assert 'error' not in data
        assert data['summary']['operations'] == 7
        # 4 planned metrics calls + 1 retried 429
        assert api.metrics_calls == 5

    def test_unconfigured_returns_configured_false(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, 'langfuse_public_key', '')
        monkeypatch.setattr(settings, 'langfuse_secret_key', '')

        data = obs_mod.ObservabilityService().get_dashboard(days=7)

        assert data == {'configured': False, 'window_days': 7}
