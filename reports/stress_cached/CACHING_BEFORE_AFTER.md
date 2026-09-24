# Redis Response Caching — Before / After

Layer 2 of the RTT optimization (after the N+1 elimination pass).

## What was added

A Redis-backed response cache (`app/services/response_cache.py`) for read-heavy
report/dashboard endpoints:

- **Short TTLs**: reports + learning dashboards = 30s, training paths = 60s.
- **Graceful degradation**: Redis down → `get`/`set` no-op → callers hit the DB
  exactly as before (same pattern as the semantic cache). Never raises.
- **Serialization**: `fastapi.jsonable_encoder` → datetimes/Decimal/sets are
  JSON-safe in Redis; responses round-trip identically to normal FastAPI output.
- **Module singleton** (`get_cache()`) — the Redis ping is paid once per
  process, not on every request.
- **Key namespaces**:
  - `resp:report:{endpoint}:{params}` — global admin/HOD report data
  - `resp:learning:{user_id}:{endpoint}` — per-user learning data
  - `resp:paths:training` — global paths listing

## Endpoints cached

| Endpoint | Key |
|---|---|
| `GET /reports/global-readiness` | `resp:report:global-readiness` |
| `GET /reports/compliance` | `resp:report:compliance` |
| `GET /reports/overdue` | `resp:report:overdue:{dept or all}` |
| `GET /reports/nq-employees` | `resp:report:nq:{dept or all}` |
| `GET /reports/token-usage` | `resp:report:token-usage:{days}` |
| `GET /reports/department-compliance/{dept}` | `resp:report:dept-compliance:{dept}` |
| `GET /learning/assigned` | `resp:learning:{uid}:assigned` |
| `GET /learning/progress/dashboard` | `resp:learning:{uid}:dashboard` |
| `GET /learning/training-record` | `resp:learning:{uid}:training-record` |
| `GET /learning/paths` | `resp:learning:{uid}:paths` |
| `GET /learning/paths/{id}/modules` | `resp:learning:{uid}:path-modules:{id}` |
| `GET /training/paths` | `resp:paths:training` |

CSV exports (`/reports/compliance/export`, `/reports/overdue/export`) **bypass
the cache** and call the service directly — this keeps `due_date.strftime()`
working on real datetime objects (a cached ISO string would crash it).

## Invalidation

Writes drop the affected cache keys (SCAN-based `delete_by_prefix`, non-blocking):

- `training_service` — create / complete / verify OJT / update / review /
  **delete** assignment → `resp:report:*`, `resp:paths:*`, affected user's keys
- `progress_service.update_progress` → user's learning keys
- `document_service` publish → `resp:report:*`, `resp:paths:*`, impacted users
- `weakness_service.record_weakness` → `resp:report:*`, user's keys (NQ counts)
- `mcq_service.submit_assessment` → `resp:report:*`, user's keys (training-record
  best score, assignment status changes)
- `user_service.create_user` + update/deactivate/activate endpoints →
  `resp:report:*` (compliance employee counts), user's keys

## Measured effect (live, same session, cold vs warm)

| Endpoint | COLD (miss) | WARM (hit) | Saved |
|---|---:|---:|---:|
| `/reports/compliance` | 1,201ms | 702ms | **499ms** |
| `/reports/overdue` | 1,239ms | 704ms | **535ms** |
| `/training/paths` | 1,140ms | 619ms | **521ms** |

Cache hits verified via TTL: repeated hits decrement TTL (30→29→28) with no
re-computation, proving the DB round-trips are skipped entirely.

## Important caveat — the auth floor

Each authenticated request still pays, before the endpoint runs:

1. `get_current_user` → one **remote (Aiven cloud) Postgres** user lookup
2. SQLAlchemy `pool_pre_ping` on session checkout

Measured floor: `/users/me` (zero endpoint DB work) = 632–1331ms, matching the
WARM timings above. So repeat loads now eliminate the endpoint's DB work
(~500ms saved) but the ~700ms auth floor remains.

**Next lever**: cache the user lookup itself (short TTL, invalidated on user
writes) to remove the auth DB round-trip — expected to take WARM loads from
~700ms down to ~50–100ms (local Redis only).
