"""Admin observability dashboard — cost, latency, volume & evals from Langfuse.

The Langfuse secret key never leaves the backend: this endpoint proxies the
Langfuse Metrics API and returns aggregates the admin UI renders. Role-gated to
admin/HOD (same policy as the other reports).
"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import require_role
from app.models.user import User, UserRole
from app.services.observability_service import ObservabilityService

router = APIRouter(prefix='/observability', tags=['observability'])

_DASHBOARD_CACHE_TTL = 300  # seconds — success payloads live 5 min (Langfuse
                            # Hobby quota: only ~100 metrics requests/day)
_ERROR_CACHE_TTL = 60       # seconds — rate-limit errors cached briefly so a
                            # throttled dashboard stops hammering the API, yet
                            # still recovers within a minute of quota reset


@router.get('/dashboard')
def get_observability_dashboard(
    days: int = Query(7, ge=1, le=30, description='Look-back window in days'),
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
):
    """Aggregated Langfuse telemetry for the admin observability dashboard.

    Returns ``{'configured': False}`` when Langfuse is not configured so the UI
    can show a setup call-to-action instead of an error.
    """
    from app.clients.langfuse_client import langfuse_configured

    if not langfuse_configured():
        return {
            'configured': False,
            'window_days': days,
            'message': 'Langfuse is not configured. Add LANGFUSE_PUBLIC_KEY / '
                       'LANGFUSE_SECRET_KEY / LANGFUSE_BASE_URL to .env to enable '
                       'cost, latency and eval dashboards.',
        }

    from app.services.response_cache import get_cache

    cache = get_cache()
    key = f'resp:observability:dashboard:{days}'
    # Successes are cached long (they are the quota-hungry path); rate-limit
    # errors get a short TTL so the dashboard recovers quickly after the
    # Langfuse quota resets instead of pinning the error for minutes.
    value = cache.get(key)
    if value is None:
        value = ObservabilityService().get_dashboard(days)
        ttl = _ERROR_CACHE_TTL if value.get('error') else _DASHBOARD_CACHE_TTL
        cache.set(key, value, ttl)
    return value
