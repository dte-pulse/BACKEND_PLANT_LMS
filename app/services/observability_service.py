"""
Admin observability service — aggregates Langfuse telemetry for the dashboard.

Reads cost / latency / volume / eval scores from the Langfuse **Metrics API v2**
(server-side proxy — the admin UI never sees the Langfuse secret key). The
metrics endpoints are the documented way to aggregate trace-level data without
fetching raw rows; the eval scores come from the ``scores-numeric`` and
``scores-boolean`` views.

Degrades gracefully:
- ``configured=False`` when Langfuse keys are absent (no network calls),
- ``error`` field when the Langfuse API is unreachable or rejects the request
  (the dashboard shows the message instead of 500ing).
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_METRICS_PATH = '/api/public/v2/metrics'
_METRICS_TIMEOUT = 30.0

# Langfuse Hobby-tier quotas are tight (100 metrics requests/day) and every
# dashboard load burns several — keep the per-request cost low and bounded.
_MAX_TRACE_PAGES = 10          # ≤ 10 × 100 trace rows per dashboard load
_MAX_RETRY_WAIT = 30           # cap on seconds we'll sleep for Retry-After
_DEFAULT_RETRY_WAIT = 5        # used when Langfuse omits Retry-After


class LangfuseRateLimitError(Exception):
    """Langfuse rejected a request with 429 — quota exhausted for this window."""


_RATE_LIMIT_MSG = (
    'Langfuse API rate limit exceeded (429 Too Many Requests). '
    'The free/Hobby plan allows ~100 metrics requests/day and each dashboard '
    'load uses several — the quota is exhausted. Try again later or upgrade '
    'the Langfuse plan.'
)


class ObservabilityService:
    def __init__(self):
        self._base_url = (settings.langfuse_base_url or 'https://cloud.langfuse.com').rstrip('/')
        self._auth = (settings.langfuse_public_key, settings.langfuse_secret_key)

    def available(self) -> bool:
        """True when Langfuse is configured (keys present + flag enabled)."""
        from app.clients.langfuse_client import langfuse_configured
        return langfuse_configured()

    # ── Public API ─────────────────────────────────────────────────────────

    def get_dashboard(self, days: int = 7) -> dict:
        """Full dashboard payload for the last ``days`` days."""
        if not self.available():
            return {'configured': False, 'window_days': days}
        try:
            data = self._fetch_aggregates(days)
            data.update({'configured': True, 'window_days': days})
            return data
        except Exception as e:  # noqa: BLE001 — never 500 the dashboard
            logger.warning('Langfuse observability dashboard failed: %s', e)
            return {
                'configured': True,
                'window_days': days,
                'error': str(e)[:300],
                'summary': None, 'by_feature': [], 'trend': [], 'evals': [],
                'recent_traces': [], 'langfuse_url': self._base_url,
            }

    # ── Metrics API v2 ─────────────────────────────────────────────────────

    def _fetch_aggregates(self, days: int) -> dict:
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
        end = now.strftime('%Y-%m-%dT%H:%M:%SZ')

        # 1. Overall totals (all observations in the window).
        totals = self._rows({
            'view': 'observations',
            'metrics': [
                {'measure': 'count', 'aggregation': 'count'},
                {'measure': 'totalCost', 'aggregation': 'sum'},
                {'measure': 'latency', 'aggregation': 'avg'},
                {'measure': 'latency', 'aggregation': 'p95'},
            ],
            'dimensions': [],
            'filters': [],
            'fromTimestamp': start,
            'toTimestamp': end,
        })
        total_row = totals[0] if totals else {}

        # 2. Per-feature breakdown + recent traces + trace count — all derived
        # from ONE paginated fetch of the traces API (NOT the metrics
        # traceName dimension, which only resolves root observations; children
        # would land in "(untraced)" and per-feature cost would be
        # misattributed). The traces API returns exact per-trace name/cost/
        # latency, and the count is exact within the page cap — so the
        # separate root-count metrics query is dropped entirely, saving one
        # rate-limited request per load on the Hobby tier.
        trace_rows = self._fetch_trace_rows(start, end)
        by_feature = self._feature_breakdown(trace_rows)
        recent_traces = self._recent_traces(trace_rows, limit=15)
        traces = len(trace_rows)

        # 4. Daily trend (operations volume + cost + latency per day).
        trend_rows = self._rows({
            'view': 'observations',
            'metrics': [
                {'measure': 'count', 'aggregation': 'count'},
                {'measure': 'totalCost', 'aggregation': 'sum'},
                {'measure': 'latency', 'aggregation': 'avg'},
                {'measure': 'latency', 'aggregation': 'p95'},
            ],
            'dimensions': [],
            'filters': [],
            'timeDimension': {'granularity': 'day'},
            'fromTimestamp': start,
            'toTimestamp': end,
            'orderBy': [{'field': 'time_dimension', 'direction': 'asc'}],
            'config': {'row_limit': 100},
        })
        trend = [
            {
                'date': str(r.get('time_dimension', ''))[:10],
                'operations': int(r.get('count_count', 0) or 0),
                'cost_usd': round(float(r.get('sum_totalCost', 0) or 0), 4),
                'avg_latency_ms': round(float(r.get('avg_latency', 0) or 0), 1),
                'p95_latency_ms': round(float(r.get('p95_latency', 0) or 0), 1),
            }
            for r in trend_rows
        ]

        # 5. Eval scores (numeric judges + boolean answer correctness).
        numeric = self._rows({
            'view': 'scores-numeric',
            'metrics': [
                {'measure': 'value', 'aggregation': 'avg'},
                {'measure': 'count', 'aggregation': 'count'},
            ],
            'dimensions': [{'field': 'name'}],
            'filters': [],
            'fromTimestamp': start,
            'toTimestamp': end,
            'config': {'row_limit': 100},
        })
        boolean = self._rows({
            'view': 'scores-boolean',
            'metrics': [
                {'measure': 'value', 'aggregation': 'avg'},
                {'measure': 'count', 'aggregation': 'count'},
            ],
            'dimensions': [{'field': 'name'}],
            'filters': [],
            'fromTimestamp': start,
            'toTimestamp': end,
            'config': {'row_limit': 100},
        })
        evals = (
            [{'name': r.get('name'), 'avg': round(float(r.get('avg_value', 0) or 0), 3),
              'count': int(r.get('count_count', 0) or 0), 'type': 'numeric'} for r in numeric]
            + [{'name': r.get('name'), 'avg': round(float(r.get('avg_value', 0) or 0), 3),
                'count': int(r.get('count_count', 0) or 0), 'type': 'boolean'} for r in boolean]
        )

        return {
            'generated_at': end,
            'summary': {
                'traces': traces,
                'operations': int(total_row.get('count_count', 0) or 0),
                'cost_usd': round(float(total_row.get('sum_totalCost', 0) or 0), 4),
                'avg_latency_ms': round(float(total_row.get('avg_latency', 0) or 0), 1),
                'p95_latency_ms': round(float(total_row.get('p95_latency', 0) or 0), 1),
            },
            'by_feature': by_feature,
            'trend': trend,
            'evals': evals,
            'recent_traces': recent_traces,
            'langfuse_url': self._base_url,
        }

    def _rows(self, query: dict) -> list[dict]:
        """Run one Metrics API v2 query and return the data rows."""
        body = self._get_json(f'{self._base_url}{_METRICS_PATH}', {'query': json.dumps(query)})
        return body.get('data', [])

    def _get_json(self, url: str, params: dict) -> dict:
        """GET a Langfuse API endpoint with one bounded 429 retry.

        Langfuse sends a ``Retry-After`` header on rate-limit responses; we
        honour it once (capped so a sync worker never blocks long) before
        surfacing a friendly rate-limit error instead of a raw HTTP trace.
        """
        resp = httpx.get(url, params=params, auth=self._auth, timeout=_METRICS_TIMEOUT)
        if resp.status_code == 429:
            retry_after = self._parse_retry_after(resp.headers.get('Retry-After'))
            wait = max(0, _DEFAULT_RETRY_WAIT if retry_after is None else retry_after)
            if wait <= _MAX_RETRY_WAIT:
                logger.warning('Langfuse 429 — retrying in %ss', wait)
                time.sleep(wait)
                resp = httpx.get(url, params=params, auth=self._auth, timeout=_METRICS_TIMEOUT)
            else:
                logger.warning('Langfuse 429 — Retry-After %ss exceeds cap, surfacing rate-limit error', wait)
            if resp.status_code == 429:
                raise LangfuseRateLimitError(_RATE_LIMIT_MSG)
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _parse_retry_after(value) -> Optional[int]:
        """Parse a Retry-After header (integer seconds); None when absent/malformed."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _fetch_trace_rows(self, start: str, end: str, page_limit: int = 100,
                          max_pages: int = _MAX_TRACE_PAGES) -> list[dict]:
        """All trace rows in the window, newest first (paginated).

        Bounded to ``max_pages × page_limit`` rows so one dashboard load can't
        fan out into dozens of general-API requests (also rate-limited on
        Hobby).
        """
        rows: list[dict] = []
        page = 1
        while page <= max_pages:
            body = self._get_json(
                f'{self._base_url}/api/public/traces',
                {'limit': page_limit, 'page': page, 'fromTimestamp': start, 'toTimestamp': end},
            )
            page_rows = body.get('data', []) or []
            if not page_rows:
                break
            rows.extend(page_rows)
            meta = body.get('meta', {})
            total_pages = meta.get('totalPages') if isinstance(meta, dict) else None
            if total_pages is not None and page >= total_pages:
                break
            page += 1
        return rows

    @staticmethod
    def _recent_traces(trace_rows: list[dict], limit: int = 15) -> list[dict]:
        """Latest traces (rows are already newest-first) for the drill-down list.

        ``html_path`` is Langfuse's relative trace URL — the UI builds the full
        link from ``langfuse_url`` so admins can open the exact trace. In-progress
        traces (no end yet) map ``latency_ms`` to None so the UI shows '—'.
        """
        out = []
        for t in trace_rows[:limit]:
            latency = t.get('latency')
            out.append({
                'id': t.get('id'),
                'name': t.get('name') or '(untraced)',
                'timestamp': t.get('timestamp'),
                'latency_ms': round(float(latency) * 1000.0, 1) if latency is not None else None,
                'cost_usd': round(float(t.get('totalCost') or 0.0), 4),
                'user_id': t.get('userId'),
                'level': t.get('level'),
                'html_path': t.get('htmlPath') or '',
            })
        return out

    @staticmethod
    def _feature_breakdown(trace_rows: list[dict]) -> list[dict]:
        """Group traces by their name and aggregate cost/latency/volume."""
        agg: dict[str, dict] = {}
        for t in trace_rows:
            name = t.get('name') or '(untraced)'
            bucket = agg.setdefault(name, {'ops': 0, 'cost': 0.0, 'latencies': []})
            bucket['ops'] += 1
            bucket['cost'] += float(t.get('totalCost') or 0.0)
            latency = t.get('latency')
            if latency is not None:
                bucket['latencies'].append(float(latency) * 1000.0)  # s → ms

        out = []
        for name, b in agg.items():
            lat = sorted(b['latencies'])
            p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0.0
            avg = sum(lat) / len(lat) if lat else 0.0
            out.append({
                'feature': name,
                'operations': b['ops'],
                'cost_usd': round(b['cost'], 4),
                'avg_latency_ms': round(avg, 1),
                'p95_latency_ms': round(p95, 1),
            })
        out.sort(key=lambda f: f['cost_usd'], reverse=True)
        return out
