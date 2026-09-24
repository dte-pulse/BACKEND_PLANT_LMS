#!/usr/bin/env python3
"""
Frontend-page stress test / round-trip latency harness.

For every page component in the frontend, this tool replays the API actions that
page performs on load + user interaction, measures the backend round-trip time
(RTT) for each request, and aggregates per-endpoint + per-page statistics
(p50 / p95 / p99 / mean / max / error rate).

Modes:
  --mode page      simulate each page's call sequence (per-role tokens)
  --mode endpoint  stress every unique endpoint individually under concurrency
  --mode both      run both (default)

Usage:
  python scripts/stress_test.py [--base-url http://127.0.0.1:8000]
                                [--iterations 10] [--concurrency 5]
                                [--mode both] [--report-dir reports]
                                [--include-writes]
"""
import argparse
import concurrent.futures
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

# ── Seed credentials (see docs/Plant-LMS_All_Endpoints.md) ──────────────────
SEED_USERS = {
    'admin':   {'employee_code': 'admin',   'password': 'adminpassword'},
    'trainer': {'employee_code': 'trainer', 'password': 'trainerpassword'},
    'trainee': {'employee_code': 'trainee', 'password': 'traineepassword'},
    'hod':     {'employee_code': 'hod',     'password': 'hodpassword'},
}

# Backend mounts all routes under /api/v1 (matches the frontend axios baseURL).
DEFAULT_BASE_URL = 'http://127.0.0.1:8000/api/v1'
TIMEOUT = 30.0


# ── Page → action map ────────────────────────────────────────────────────────
# Each action: (method, path_template, role, body_factory_or_None, is_write)
# {id} placeholders are resolved from the live API before the run.
def _pages() -> dict:
    return {
        'auth/LoginPage': [
            ('POST', '/auth/login', 'admin', lambda ctx: {'employee_code': 'admin', 'password': 'adminpassword'}, True),
            ('GET', '/users/me', 'admin', None, False),
        ],
        'admin/DashboardPage': [
            ('GET', '/reports/global-readiness', 'admin', None, False),
            ('GET', '/reports/overdue', 'admin', None, False),
            ('GET', '/reports/nq-employees', 'admin', None, False),
        ],
        'admin/DocumentsPage': [
            ('GET', '/documents', 'admin', None, False),
            ('GET', '/subjects', 'admin', None, False),
            ('GET', '/topics', 'admin', None, False),
            ('GET', '/documents/{doc_id}', 'admin', None, False),
            ('GET', '/documents/{doc_id}/chunks', 'admin', None, False),
            ('GET', '/documents/{doc_id}/history-summary', 'admin', None, False),
            ('GET', '/documents/{doc_id}/view-url', 'admin', None, False),
        ],
        'admin/MasterDataPage': [
            ('GET', '/departments', 'admin', None, False),
            ('GET', '/departments/{dept_id}', 'admin', None, False),
            ('GET', '/subjects', 'admin', None, False),
            ('GET', '/topics', 'admin', None, False),
            ('GET', '/topics/{topic_id}', 'admin', None, False),
        ],
        'admin/NotificationsPage': [
            ('GET', '/notifications', 'admin', None, False),
            ('GET', '/notifications/unread-count', 'admin', None, False),
            ('POST', '/notifications/{notification_id}/read', 'admin', None, True),
        ],
        'admin/ReportsPage': [
            ('GET', '/reports/compliance', 'admin', None, False),
            ('GET', '/reports/overdue', 'admin', None, False),
            ('GET', '/reports/nq-employees', 'admin', None, False),
            ('GET', '/reports/global-readiness', 'admin', None, False),
            ('GET', '/reports/token-usage', 'admin', None, False),
        ],
        'admin/TrainingPage': [
            ('GET', '/training/assignments', 'admin', None, False),
            ('GET', '/users', 'admin', None, False),
            ('GET', '/documents', 'admin', None, False),
        ],
        'admin/UsersPage': [
            ('GET', '/users', 'admin', None, False),
            ('GET', '/users/{user_id}', 'admin', None, False),
            ('GET', '/users/{user_id}/progress', 'admin', None, False),
        ],
        'hod/CalendarPage': [
            ('GET', '/calendar/events', 'hod', None, False),
            ('GET', '/calendar/upcoming', 'hod', None, False),
        ],
        'hod/HodDashboardPage': [
            ('GET', '/users/me', 'hod', None, False),
            ('GET', '/reports/department-compliance/{dept_name}', 'hod', None, False),
        ],
        'hod/QualificationPage': [
            ('GET', '/users/me', 'hod', None, False),
            ('GET', '/documents', 'hod', None, False),
            ('GET', '/training/assignments/{assignment_id}/evidence', 'hod', None, False),
        ],
        'hod/ReportsPage': [
            ('GET', '/users/me', 'hod', None, False),
            ('GET', '/reports/department-compliance/{dept_name}', 'hod', None, False),
        ],
        'trainee/AssessmentsPage': [
            ('GET', '/learning/assigned', 'trainee', None, False),
            ('GET', '/documents', 'trainee', None, False),
            ('GET', '/documents/{doc_id}/chunks', 'trainee', None, False),
            ('GET', '/mcq/document/{doc_id}', 'trainee', None, False),
            ('GET', '/mcq/document/{doc_id}/final-assessment', 'trainee', None, False),
            ('GET', '/learning/session/document/{doc_id}/mindmap', 'trainee', None, False),
        ],
        'trainee/LearnSessionPage': [
            ('GET', '/documents/{doc_id}', 'trainee', None, False),
            ('GET', '/learning/session/document/{doc_id}/structure', 'trainee', None, False),
            ('GET', '/learning/session/child/{chunk_id}/question', 'trainee', None, False),
            ('GET', '/learning/session/document/{doc_id}/mindmap', 'trainee', None, False),
        ],
        'trainee/MindMapPage': [
            ('GET', '/learning/session/document/{doc_id}/mindmap', 'trainee', None, False),
        ],
        'trainee/ProgressPage': [
            ('GET', '/learning/progress/dashboard', 'trainee', None, False),
            ('GET', '/learning/training-record', 'trainee', None, False),
        ],
        'trainee/QaPage': [
            ('GET', '/learning/assigned', 'trainee', None, False),
            ('GET', '/qa?document_id={doc_id}', 'trainee', None, False),
            ('GET', '/learning/session/document/{doc_id}/mindmap', 'trainee', None, False),
            ('POST', '/qa', 'trainee', lambda ctx: {
                'document_id': int(ctx['doc_id']), 'question': 'What is the purpose of this document?'}, True),
        ],
        'trainee/TraineeDashboardPage': [
            ('GET', '/learning/progress/dashboard', 'trainee', None, False),
        ],
        'trainee/TraineePathsPage': [
            ('GET', '/learning/paths', 'trainee', None, False),
        ],
        'trainer/AssessmentsPage': [
            ('GET', '/documents', 'trainer', None, False),
            ('GET', '/mcq/document/{doc_id}', 'trainer', None, False),
        ],
        'trainer/AttendancePage': [
            ('GET', '/calendar/events', 'trainer', None, False),
        ],
        'trainer/MaterialsPage': [
            ('GET', '/documents', 'trainer', None, False),
            ('GET', '/ingestion/status/{doc_id}', 'trainer', None, False),
            ('GET', '/documents/{doc_id}/view-url', 'trainer', None, False),
        ],
        'trainer/TrainerDashboardPage': [
            ('GET', '/training/trainer/dashboard', 'trainer', None, False),
            ('GET', '/training/trainer/assignments', 'trainer', None, False),
        ],
        'trainer/TrainingPathsPage': [
            ('GET', '/training/paths', 'trainer', None, False),
            ('GET', '/documents', 'trainer', None, False),
        ],
    }


class StressRunner:
    def __init__(self, base_url: str, iterations: int, concurrency: int,
                 include_writes: bool, report_dir: Path):
        self.base_url = base_url.rstrip('/')
        self.iterations = iterations
        self.concurrency = concurrency
        self.include_writes = include_writes
        self.report_dir = report_dir
        self.client = httpx.Client(timeout=TIMEOUT, verify=False)
        self.tokens: dict[str, str] = {}
        self.ctx: dict = {}
        self.results: list[dict] = []  # one entry per request
        self._login_all()

    # ── auth ────────────────────────────────────────────────────────────────
    def _login(self, role: str) -> str:
        creds = SEED_USERS[role]
        r = self.client.post(f'{self.base_url}/auth/login', json=creds)
        r.raise_for_status()
        return r.json()['access_token']

    def _login_all(self):
        for role in SEED_USERS:
            try:
                self.tokens[role] = self._login(role)
            except Exception as e:  # noqa: BLE001
                print(f'[warn] login failed for {role}: {e}', file=sys.stderr)
        if not self.tokens:
            raise SystemExit('No role could authenticate — aborting.')
        print(f'[ok] logged in roles: {", ".join(self.tokens)}')

    # ── context discovery (real IDs from the live API) ──────────────────────
    def _discover_context(self):
        admin = self.tokens.get('admin') or next(iter(self.tokens.values()))
        h = {'Authorization': f'Bearer {admin}'}
        ctx = {}

        def first_id(url: str, key: str | None = None):
            try:
                r = self.client.get(f'{self.base_url}{url}', headers=h)
                if r.status_code != 200:
                    return None
                data = r.json()
                if isinstance(data, list) and data:
                    item = data[0]
                    return item.get(key) if key else (item.get('id') if isinstance(item, dict) else None)
                if isinstance(data, dict):
                    items = data.get('items') or data.get('data') or []
                    if items:
                        item = items[0]
                        return item.get(key) if key else (item.get('id') if isinstance(item, dict) else None)
                return None
            except Exception:  # noqa: BLE001
                return None

        ctx['doc_id'] = first_id('/documents')
        ctx['dept_id'] = first_id('/departments')
        ctx['topic_id'] = first_id('/topics')
        ctx['user_id'] = first_id('/users')
        ctx['assignment_id'] = first_id('/training/assignments')
        ctx['dept_name'] = self._first_dept_name(admin)
        ctx['chunk_id'] = self._first_chunk_id(ctx['doc_id'], admin)
        ctx['notification_id'] = self._first_notification_id(admin)
        ctx['event_id'] = self._first_event_id(admin)
        print(f'[ok] context: {json.dumps(ctx)}')

    def _first_dept_name(self, token: str) -> str:
        try:
            r = self.client.get(f'{self.base_url}/departments', headers={'Authorization': f'Bearer {token}'})
            if r.status_code == 200:
                data = r.json()
                items = data if isinstance(data, list) else (data.get('items') or [])
                if items and isinstance(items[0], dict):
                    return items[0].get('name') or 'Software Development'
        except Exception:  # noqa: BLE001
            pass
        return 'Software Development'

    def _first_chunk_id(self, doc_id, token: str):
        if not doc_id:
            return None
        try:
            r = self.client.get(f'{self.base_url}/documents/{doc_id}/chunks',
                                headers={'Authorization': f'Bearer {token}'})
            if r.status_code == 200:
                data = r.json()
                items = data if isinstance(data, list) else (data.get('chunks') or data.get('items') or [])
                if items:
                    return items[0].get('id') if isinstance(items[0], dict) else items[0]
        except Exception:  # noqa: BLE001
            pass
        return None

    def _first_notification_id(self, token: str):
        try:
            r = self.client.get(f'{self.base_url}/notifications', headers={'Authorization': f'Bearer {token}'})
            if r.status_code == 200:
                data = r.json()
                items = data if isinstance(data, list) else (data.get('items') or [])
                if items and isinstance(items[0], dict):
                    return items[0].get('id')
        except Exception:  # noqa: BLE001
            pass
        return None

    def _first_event_id(self, token: str):
        try:
            r = self.client.get(f'{self.base_url}/calendar/events', headers={'Authorization': f'Bearer {token}'})
            if r.status_code == 200:
                data = r.json()
                items = data if isinstance(data, list) else (data.get('items') or [])
                if items and isinstance(items[0], dict):
                    return items[0].get('id')
        except Exception:  # noqa: BLE001
            pass
        return None

    # ── request execution ───────────────────────────────────────────────────
    def _resolve(self, path: str) -> str | None:
        """Fill {placeholders} from ctx; None if an ID is missing (skip)."""
        for key, val in self.ctx.items():
            ph = '{' + key + '}'
            if ph in path:
                if val is None:
                    return None
                path = path.replace(ph, str(val))
        return path

    def _do_request(self, method: str, path: str, role: str, body) -> tuple[float, int | None, str]:
        token = self.tokens.get(role)
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        url = f'{self.base_url}{path}'
        t0 = time.perf_counter()
        try:
            r = self.client.request(method, url, json=body, headers=headers)
            elapsed = (time.perf_counter() - t0) * 1000.0
            return elapsed, r.status_code, ''
        except httpx.TimeoutException:
            return (time.perf_counter() - t0) * 1000.0, None, 'timeout'
        except Exception as e:  # noqa: BLE001
            return (time.perf_counter() - t0) * 1000.0, None, str(e)[:120]

    def _record(self, page: str, method: str, path: str, ms: float, status, err: str):
        self.results.append({
            'page': page, 'method': method, 'endpoint': path,
            'latency_ms': round(ms, 2), 'status': status, 'error': err,
        })

    # ── modes ───────────────────────────────────────────────────────────────
    def run_page_mode(self, pages: dict):
        print(f'\n=== PAGE MODE: replaying {len(pages)} pages, {self.iterations} iterations ===')
        actions = []
        for page, calls in pages.items():
            for call in calls:
                if call[4] and not self.include_writes:
                    continue  # skip write actions unless enabled
                actions.append((page, call))
        total = len(actions) * self.iterations
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = []
            for _ in range(self.iterations):
                for page, (method, path, role, body_fn, _w) in actions:
                    futures.append(pool.submit(self._page_action, page, method, path, role, body_fn))
            for fut in concurrent.futures.as_completed(futures):
                done += 1
                if done % 50 == 0 or done == total:
                    print(f'  page-mode progress: {done}/{total}')
        print('[ok] page mode complete')

    def _page_action(self, page, method, path, role, body_fn):
        resolved = self._resolve(path)
        if resolved is None:
            return
        body = body_fn(self.ctx) if body_fn else None
        ms, status, err = self._do_request(method, resolved, role, body)
        self._record(page, method, path, ms, status, err)

    def run_endpoint_mode(self, pages: dict):
        print(f'\n=== ENDPOINT MODE: stressing unique endpoints, {self.iterations} iters x {self.concurrency} workers ===')
        # unique endpoints grouped by role (use admin token for shared reads)
        seen: dict[tuple[str, str], tuple] = {}
        for page, calls in pages.items():
            for call in calls:
                if call[4] and not self.include_writes:
                    continue
                method, path, role, body_fn, _w = call
                key = (method, path)
                if key not in seen:
                    seen[key] = (page, method, path, role, body_fn)

        def worker(item):
            page, method, path, role, body_fn = item
            resolved = self._resolve(path)
            if resolved is None:
                return
            body = body_fn(self.ctx) if body_fn else None
            ms, status, err = self._do_request(method, resolved, role, body)
            self._record(page, method, path, ms, status, err)

        items = list(seen.values())
        total = len(items) * self.iterations
        done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = []
            for _ in range(self.iterations):
                for item in items:
                    futures.append(pool.submit(worker, item))
            for _ in concurrent.futures.as_completed(futures):
                done += 1
                if done % 50 == 0 or done == total:
                    print(f'  endpoint-mode progress: {done}/{total}')
        print('[ok] endpoint mode complete')

    # ── report ──────────────────────────────────────────────────────────────
    def _stats(self, lats: list[float]) -> dict:
        if not lats:
            return {'count': 0}
        lats_sorted = sorted(lats)
        n = len(lats_sorted)

        def pct(p):
            idx = min(n - 1, int(p * n))
            return round(lats_sorted[idx], 2)

        return {
            'count': n,
            'mean_ms': round(statistics.mean(lats_sorted), 2),
            'min_ms': round(lats_sorted[0], 2),
            'max_ms': round(lats_sorted[-1], 2),
            'p50_ms': pct(0.50),
            'p95_ms': pct(0.95),
            'p99_ms': pct(0.99),
        }

    def build_report(self) -> tuple[dict, str]:
        # group by endpoint
        by_endpoint: dict[str, list[float]] = {}
        by_endpoint_status: dict[str, list[int | None]] = {}
        by_endpoint_err: dict[str, int] = {}
        by_page: dict[str, list[float]] = {}
        for r in self.results:
            ep = f"{r['method']} {r['endpoint']}"
            by_endpoint.setdefault(ep, []).append(r['latency_ms'])
            by_endpoint_status.setdefault(ep, []).append(r['status'])
            if r['error']:
                by_endpoint_err[ep] = by_endpoint_err.get(ep, 0) + 1
            by_page.setdefault(r['page'], []).append(r['latency_ms'])

        endpoint_stats = {}
        for ep, lats in sorted(by_endpoint.items()):
            stats = self._stats(lats)
            statuses = by_endpoint_status[ep]
            ok = sum(1 for s in statuses if s is not None and 200 <= s < 300)
            stats['errors'] = by_endpoint_err.get(ep, 0)
            stats['error_rate'] = round(stats['errors'] / stats['count'], 4) if stats['count'] else 0.0
            stats['http_2xx'] = ok
            endpoint_stats[ep] = stats

        page_stats = {pg: self._stats(lats) for pg, lats in sorted(by_page.items())}

        summary = {
            'generated_at': datetime.utcnow().isoformat() + 'Z',
            'base_url': self.base_url,
            'iterations': self.iterations,
            'concurrency': self.concurrency,
            'include_writes': self.include_writes,
            'total_requests': len(self.results),
            'pages_tested': len(by_page),
            'endpoints_tested': len(by_endpoint),
            'slowest_endpoints': sorted(
                ((ep, s['p95_ms'], s['error_rate']) for ep, s in endpoint_stats.items()),
                key=lambda t: t[1], reverse=True)[:10],
            'endpoints': endpoint_stats,
            'pages': page_stats,
        }

        # markdown
        md = [f"# Frontend Page → Backend RTT Stress Report",
              f"\nGenerated: {summary['generated_at']}  ·  Base: `{self.base_url}`  ·  "
              f"{summary['total_requests']} requests  ·  {summary['endpoints_tested']} endpoints  ·  "
              f"iterations={self.iterations}  concurrency={self.concurrency}",
              "\n## Slowest endpoints by p95",
              "\n| p95 ms | error rate | endpoint |",
              "|---|---:|---|"]
        for ep, p95, err in summary['slowest_endpoints']:
            md.append(f"| {p95} | {err:.2%} | `{ep}` |")

        md.append("\n## All endpoints")
        md.append("\n| endpoint | count | mean ms | p50 | p95 | p99 | max | err% |")
        md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
        for ep, s in endpoint_stats.items():
            md.append(f"| `{ep}` | {s['count']} | {s['mean_ms']} | {s['p50_ms']} | "
                      f"{s['p95_ms']} | {s['p99_ms']} | {s['max_ms']} | {s['error_rate']:.2%} |")

        md.append("\n## Per page (aggregate of page's requests)")
        md.append("\n| page | count | mean ms | p50 | p95 | p99 | max |")
        md.append("|---|---:|---:|---:|---:|---:|---:|")
        for pg, s in page_stats.items():
            md.append(f"| `{pg}` | {s['count']} | {s['mean_ms']} | {s['p50_ms']} | "
                      f"{s['p95_ms']} | {s['p99_ms']} | {s['max_ms']} |")

        return summary, '\n'.join(md) + '\n'


def main():
    ap = argparse.ArgumentParser(description='Frontend page → backend RTT stress test')
    ap.add_argument('--base-url', default=DEFAULT_BASE_URL,
                help='full base including /api/v1, e.g. http://127.0.0.1:8000/api/v1')
    ap.add_argument('--iterations', type=int, default=10)
    ap.add_argument('--concurrency', type=int, default=5)
    ap.add_argument('--mode', choices=['page', 'endpoint', 'both'], default='both')
    ap.add_argument('--report-dir', default='reports/stress')
    ap.add_argument('--include-writes', action='store_true',
                    help='include write actions (login, mark-read, QA post) in the load')
    args = ap.parse_args()

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    runner = StressRunner(args.base_url, args.iterations, args.concurrency,
                          args.include_writes, report_dir)
    runner._discover_context()

    pages = _pages()
    if args.mode in ('page', 'both'):
        runner.run_page_mode(pages)
    if args.mode in ('endpoint', 'both'):
        runner.run_endpoint_mode(pages)

    summary, md = runner.build_report()
    stamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    json_path = report_dir / f'stress_{stamp}.json'
    md_path = report_dir / f'stress_{stamp}.md'
    json_path.write_text(json.dumps(summary, indent=2))
    md_path.write_text(md)
    print(f'\n[report] JSON: {json_path}')
    print(f'[report] MD:   {md_path}')
    print('\n=== TOP 10 SLOWEST ENDPOINTS (p95) ===')
    for ep, p95, err in summary['slowest_endpoints']:
        print(f'  {p95:>8.1f} ms  err={err:.2%}  {ep}')


if __name__ == '__main__':
    main()
