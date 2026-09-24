"""
Redis-backed response cache for read-heavy report/dashboard endpoints.

Motivation: after the N+1 pass, the remaining latency floor is the remote
(Aiven cloud) Postgres RTT per request. Report/dashboard endpoints are
read-heavy and tolerate short staleness, so we cache the serialized response
for a few seconds — repeat page loads skip the DB round-trips entirely.

Design:
- Short TTL (default 30s) — dashboards stay fresh while repeat loads are instant.
- Graceful degradation: if Redis is down, get()/set() no-op and callers fall
  through to the DB exactly as before (same pattern as SemanticCacheService).
- Values are serialized with fastapi.jsonable_encoder so datetimes/ORM-safe
  types round-trip through Redis cleanly.
- Key namespaces:
    resp:report:{endpoint}:{params}      — global admin/hod report data
    resp:learning:{user_id}:{endpoint}   — per-user learning data
    resp:paths:{kind}                    — global path listings
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from fastapi.encoders import jsonable_encoder

logger = logging.getLogger(__name__)

# Short TTLs — dashboards must not go stale for long.
REPORT_CACHE_TTL = 30       # seconds
LEARNING_CACHE_TTL = 30     # seconds
PATHS_CACHE_TTL = 60        # seconds

# Sentinel so a cached JSON `null` (factory returning None) is distinguishable
# from a miss — otherwise such values would never be cached.
_MISS = object()


class ResponseCache:
    """Best-effort Redis cache. Never raises; callers always fall back to DB."""

    def __init__(self):
        self._redis = None
        self._available = False
        try:
            from app.core.redis import redis_client
            self._redis = redis_client
            self._redis.ping()
            self._available = True
        except Exception as e:
            logger.warning(f"ResponseCache: Redis unavailable, caching disabled. {e}")

    @property
    def available(self) -> bool:
        return self._available

    def get(self, key: str) -> Any:
        """Return the cached value, or None on miss / cache disabled."""
        if not self._available:
            return None
        try:
            raw = self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.error(f"ResponseCache get error for {key}: {e}")
            return None

    def set(self, key: str, value: Any, ttl: int = REPORT_CACHE_TTL) -> None:
        if not self._available:
            return
        try:
            self._redis.setex(key, ttl, json.dumps(jsonable_encoder(value)))
        except Exception as e:
            logger.error(f"ResponseCache set error for {key}: {e}")

    def get_or_set(self, key: str, ttl: int, factory: Callable[[], Any]) -> Any:
        """Return cached value or compute via factory (miss → compute + store).
        Uses an internal sentinel so a factory returning None is still cached
        (stored as JSON null) instead of being recomputed on every request."""
        if not self._available:
            return factory()
        try:
            raw = self._redis.get(key)
            if raw is not None:
                return json.loads(raw)
        except Exception as e:
            logger.error(f"ResponseCache get_or_set get error for {key}: {e}")
        value = factory()
        self.set(key, value, ttl)
        return value

    def delete(self, *keys: str) -> None:
        if not self._available or not keys:
            return
        try:
            self._redis.delete(*keys)
        except Exception as e:
            logger.error(f"ResponseCache delete error: {e}")

    def delete_by_prefix(self, prefix: str) -> None:
        """Delete all keys starting with prefix (cursor SCAN, non-blocking)."""
        if not self._available:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = self._redis.scan(cursor, match=f"{prefix}*", count=200)
                if keys:
                    self._redis.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.error(f"ResponseCache delete_by_prefix error for {prefix}: {e}")


# Module-level singleton — constructed once so the Redis ping is paid a single
# time per process instead of on every request (review fix #1).
_instance: Optional[ResponseCache] = None


def get_cache() -> ResponseCache:
    global _instance
    if _instance is None:
        _instance = ResponseCache()
    return _instance


def invalidate_cached(*prefixes: str) -> None:
    """Best-effort invalidation of all cache keys under the given prefixes.
    Call from write paths that change report/dashboard/learning data."""
    try:
        cache = get_cache()
        for prefix in prefixes:
            cache.delete_by_prefix(prefix)
    except Exception as e:
        logger.error(f"ResponseCache invalidate_cached error: {e}")
