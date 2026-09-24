# RTT Stress Test — Before vs After (N+1 Elimination)

Same harness: `iterations=3, concurrency=5, mode=both` (318 requests, 40 endpoints).
Baseline: `reports/stress_baseline/stress_20260811_095531.md` (2026-08-11 09:55)
After:    `reports/stress_after/stress_20260811_100208.md` (2026-08-11 10:02)

## Endpoint-level (p95, ms)

| Endpoint | Before | After | Change |
|---|---:|---:|---:|
| `GET /reports/compliance` | 5355 | 381 | **−93%** |
| `GET /reports/overdue` | 3423 | 736 | **−79%** |
| `GET /reports/global-readiness` | 3237 | 633 | **−80%** |
| `GET /reports/nq-employees` | 2942 | 586 | **−80%** |
| `GET /training/paths` | 2602 | 325 | **−88%** |
| `GET /learning/assigned` | 2350 | 406 | **−83%** |
| `GET /documents` | 2148 | 328 | **−85%** |
| `GET /learning/paths` | 2129 | 433 | **−80%** |
| `GET /learning/training-record` | 2082 | 348 | **−83%** |
| `GET /reports/token-usage` | 2060 | 421 | **−80%** |
| `GET /notifications` | 2052 | 264 | **−87%** |
| `GET /topics` | 2044 | 293 | **−86%** |
| `GET /users` | 1978 | 302 | **−85%** |

## Page-level (mean, ms)

| Page | Before | After | Change |
|---|---:|---:|---:|
| `admin/ReportsPage` | 2232 | 306 | **−86%** |
| `trainer/TrainingPathsPage` | 1738 | 273 | **−84%** |
| `admin/DashboardPage` | 1355 | 368 | **−73%** |
| `trainee/TraineePathsPage` | 1352 | 408 | **−70%** |
| `trainee/ProgressPage` | 1201 | 316 | **−74%** |
| `trainee/QaPage` | 819 | 317 | **−61%** |
| `admin/DocumentsPage` | 835 | 228 | **−73%** |

## What changed

Killed N+1 query loops in the slow read paths (per-row lookups → single batched
`IN` queries + in-memory aggregation):

- **`report_service.py`** — compliance (per-user assignment + weakness queries),
  overdue (per-assignment user/doc lookups), nq-employees (per-weakness user +
  weakness re-query), user training history (per-assignment doc + best-score),
  global-readiness (3 COUNTs → 1 aggregate query).
- **`learning.py`** — `/learning/assigned`, `/learning/progress/dashboard`,
  `/learning/training-record`, `/learning/paths`, `/learning/paths/{id}/modules`
  (per-assignment doc/progress/topic lookups → batched).
- **`training.py`** — `/training/paths` (per-topic document COUNT → 1 grouped query).
- **`trainer_service.py`** — trainer assignments (per-assignment trainee/doc lookups).
- **`document_service.py`** — publish-impact collection (per-user lookups → batched).
- **`annexure_service.py`** — Annexure I/II/IV/V/XI HTML generators (per-row
  doc/user/attempt lookups → batched).

## Remaining observations

- Error rate is **0.00%** in both runs.
- Even untouched single-query endpoints improved (~85%) because the bottleneck was
  **remote (Aiven cloud) Postgres RTT × query count** — with per-request query
  counts collapsed, overall DB round-trips dropped.
- `GET /ingestion/status/{doc_id}` (470ms p95) and `GET /qa` (413ms) are the new
  slowest; both are single-query paths (qa includes an LLM call).
- Next lever if more speed is needed: connection pooling / Redis response caching
  (not yet applied — was out of scope for this N+1 pass).
