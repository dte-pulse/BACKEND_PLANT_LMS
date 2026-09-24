"""
Phase 3/4 — Real semantic cache service using Redis for Q&A cost reduction.
Strategy: cosine similarity >= 0.92 on cached query embeddings → cache hit.
"""
import json
import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days
SIMILARITY_THRESHOLD = 0.92

# P1 #3: was 200. Each entry carries a full query embedding (~1536 floats as
# JSON — tens of KB), and lookup deserializes the WHOLE list per question, so
# 200 entries made every cache probe needlessly heavy. 50 is plenty of
# coverage per doc+version and keeps probes fast.
MAX_ENTRIES_PER_KEY = 50

# Atomic read-append-set. The old GET→append→SETEX in Python lost entries when
# two questions stored concurrently (last writer won). The Lua script runs
# atomically inside Redis, so concurrent stores can no longer clobber each
# other. Also trims to MAX_ENTRIES_PER_KEY and refreshes the TTL in one step.
_STORE_SCRIPT = """
local raw = redis.call('GET', KEYS[1])
local entries = {}
if raw then
    local ok, decoded = pcall(cjson.decode, raw)
    if ok and type(decoded) == 'table' then entries = decoded end
end
table.insert(entries, cjson.decode(ARGV[1]))
local n = #entries
local max_n = tonumber(ARGV[2])
if n > max_n then
    local trimmed = {}
    for i = n - max_n + 1, n do trimmed[i - (n - max_n)] = entries[i] end
    entries = trimmed
    n = max_n
end
redis.call('SETEX', KEYS[1], tonumber(ARGV[3]), cjson.encode(entries))
return n
"""
_store_script = None  # registered lazily (redis-py Script object)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticCacheService:
    """
    Redis-backed semantic cache for Q&A responses.
    Cache key prefix: qa_cache:{document_id}:{topic_id}
    Each key stores a list of {embedding, question, answer} entries.
    """

    def __init__(self):
        self._redis = None
        self._available = False
        try:
            from app.core.redis import redis_client
            self._redis = redis_client
            self._redis.ping()
            self._available = True
            logger.info("SemanticCacheService: Unified Redis connected")
        except Exception as e:
            logger.warning(f"SemanticCacheService: Redis unavailable, cache disabled. {e}")

    @property
    def available(self) -> bool:
        return self._available

    def _cache_key(self, document_id: int, topic_id: Optional[int] = None, doc_version: Optional[int] = None) -> str:
        # G-6: include document version so old-version cache entries never pollute
        # queries that have been resolved to a newer document version.
        ver_part = f":v{doc_version}" if doc_version is not None else ""
        return f"qa_cache:{document_id}{ver_part}:{topic_id or 'all'}"

    def lookup(
        self,
        document_id: int,
        query_embedding: list[float],
        topic_id: Optional[int] = None,
        doc_version: Optional[int] = None,
    ) -> Optional[dict]:
        """Check cache for a semantically similar question.

        R-6 fix: returns the full cache ENTRY (answer + source chunk provenance)
        instead of a bare string, so cached answers can still render citations.
        Returns None on miss.
        """
        if not self._available:
            return None
        try:
            key = self._cache_key(document_id, topic_id, doc_version=doc_version)
            raw = self._redis.get(key)
            if not raw:
                self._record_counter('qa_cache:misses')
                return None
            entries: list[dict] = json.loads(raw)
            for entry in entries:
                sim = _cosine_similarity(query_embedding, entry['embedding'])
                if sim >= SIMILARITY_THRESHOLD:
                    logger.info(f"Cache HIT (similarity={sim:.4f}) for doc={document_id}")
                    self._record_counter('qa_cache:hits')
                    return entry
            self._record_counter('qa_cache:misses')
            return None
        except Exception as e:
            logger.error(f"Cache lookup error: {e}")
            return None

    def store(
        self,
        document_id: int,
        question: str,
        query_embedding: list[float],
        answer: str,
        topic_id: Optional[int] = None,
        doc_version: Optional[int] = None,
        source_chunk_ids: Optional[list[int]] = None,
        page_refs: Optional[list[int]] = None,
    ):
        """Store a Q&A pair in the cache, including source provenance (R-6).

        P1 #3: the append runs as an atomic Lua script inside Redis —
        concurrent stores can no longer lose entries, trimming and TTL
        refresh happen in the same atomic step.
        """
        if not self._available:
            return
        try:
            global _store_script
            if _store_script is None:
                _store_script = self._redis.register_script(_STORE_SCRIPT)
            key = self._cache_key(document_id, topic_id, doc_version=doc_version)
            entry = {
                'question': question,
                'embedding': query_embedding,
                'answer': answer,
                'source_chunk_ids': source_chunk_ids or [],
                'page_refs': page_refs or [],
            }
            _store_script(
                keys=[key],
                args=[json.dumps(entry), MAX_ENTRIES_PER_KEY, CACHE_TTL_SECONDS],
            )
        except Exception as e:
            logger.error(f"Cache store error: {e}")

    def _record_counter(self, name: str):
        """T-3: lightweight hit/miss counters (best-effort, never raise)."""
        try:
            if self._available:
                self._redis.incr(f'stats:{name}')
        except Exception as e:
            logger.debug(f'Failed to record counter {name}: {e}')

    def get_counters(self) -> dict:
        """T-3: return hit/miss counters (best-effort)."""
        try:
            if not self._available:
                return {}
            hits = int(self._redis.get('stats:qa_cache:hits') or 0)
            misses = int(self._redis.get('stats:qa_cache:misses') or 0)
            hit_rate = round(hits / (hits + misses), 4) if (hits + misses) else 0.0
            return {'hits': hits, 'misses': misses, 'hit_rate': hit_rate}
        except Exception as e:
            logger.error(f'Cache counter read error: {e}')
            return {}

    def invalidate_document(self, document_id: int):
        """Invalidate all cache entries for a document (call on doc update).
        Uses cursor-based SCAN instead of KEYS to avoid blocking Redis (G-19)."""
        if not self._available:
            return
        try:
            pattern = f"qa_cache:{document_id}:*"
            cursor = 0
            deleted = 0
            while True:
                cursor, keys = self._redis.scan(cursor, match=pattern, count=100)
                if keys:
                    self._redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
            if deleted:
                logger.info(f"Invalidated {deleted} cache keys for document {document_id}")
        except Exception as e:
            logger.error(f"Cache invalidation error: {e}")
