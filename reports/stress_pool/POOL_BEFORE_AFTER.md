# DB Pool — Idle-Ping (replaces pool_pre_ping)

Layer 3 of the RTT optimization (after N+1 elimination + Redis response caching).

## What changed

`app/db/session.py` — removed `pool_pre_ping=True` (which executed a remote-DB
`SELECT 1` round-trip on **every** session checkout, ~400ms on cloud Postgres)
and replaced it with idle-scoped validation:

- `connect` → stamp connection as freshly used
- `reset` → stamp time the connection returns to the pool (idle clock starts)
- `checkout` → **ping only if idle ≥ `db_idle_ping_seconds` (30s)**; on ping
  failure raise `DisconnectionError` so SQLAlchemy invalidates the stale
  connection and retries with a fresh one (same safety net as pre_ping,
  scoped to idle connections only)

Setting: `db_idle_ping_seconds: int = 30` in `app/core/config.py`.

## Measured effect (live, hot pool)

| Metric | Before (pre_ping) | After (idle-ping) |
|---|---:|---:|
| `/users/me` auth floor (mean) | ~667ms | **250ms** |
| `/users/me` range | 632–1331ms | **188–385ms** |
| cached `/reports/compliance` (mean) | ~700–840ms | **353ms** (min 182ms) |

The auth floor dropped ~62% — the per-request DB ping is gone; only the auth
user lookup round-trip remains.

## Full-suite results (iterations=3, concurrency=5, 318 requests)

| Endpoint | Baseline p95 | N+1 p95 | +Cache p95 | +Pool mean |
|---|---:|---:|---:|---:|
| `/reports/compliance` | 5,355 | 381 | 2,064* | **197** |
| `/reports/overdue` | 3,423 | 736 | 2,343* | **299** |
| `/reports/global-readiness` | 3,237 | 633 | 1,969* | **296** |
| `/reports/nq-employees` | 2,942 | 586 | 1,970* | **295** |
| `/training/paths` | 2,602 | 325 | 944 | **202** |
| `/learning/assigned` | 2,350 | 406 | 1,667* | **232** |
| `/learning/training-record` | 2,082 | 348 | 1,684* | **209** |
| `/learning/paths` | 2,129 | 433 | 1,664* | **223** |
| `/learning/progress/dashboard` | 1,413 | 324 | 1,719* | **196** |

\* The "+Cache" run happened during a remote-DB slow patch (even `/health`'s
`SELECT 1` timed out), so those p95s are inflated. The +Pool run (mean column)
shows the settled numbers: **every endpoint is now < 300ms mean**, with the
occasional p95 spike (~1.1s) caused by 30s cache-TTL expiry recomputing the
report from the DB.

Error rate 0.00% throughout.

## Notes

- The idle-ping threshold is intentionally longer than typical inter-request
  gaps, so hot connections skip validation entirely while genuinely idle
  connections (>30s) are still guarded.
- `pool_recycle=300` remains as a backstop for very long-lived connections.
