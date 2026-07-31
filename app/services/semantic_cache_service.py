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

    def _cache_key(self, document_id: int, topic_id: Optional[int] = None) -> str:
        return f"qa_cache:{document_id}:{topic_id or 'all'}"

    def lookup(
        self,
        document_id: int,
        query_embedding: list[float],
        topic_id: Optional[int] = None,
    ) -> Optional[str]:
        """Check cache for a semantically similar question. Returns cached answer or None."""
        if not self._available:
            return None
        try:
            key = self._cache_key(document_id, topic_id)
            raw = self._redis.get(key)
            if not raw:
                return None
            entries: list[dict] = json.loads(raw)
            for entry in entries:
                sim = _cosine_similarity(query_embedding, entry['embedding'])
                if sim >= SIMILARITY_THRESHOLD:
                    logger.info(f"Cache HIT (similarity={sim:.4f}) for doc={document_id}")
                    return entry['answer']
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
    ):
        """Store a Q&A pair in the cache."""
        if not self._available:
            return
        try:
            key = self._cache_key(document_id, topic_id)
            raw = self._redis.get(key)
            entries: list[dict] = json.loads(raw) if raw else []
            entries.append({
                'question': question,
                'embedding': query_embedding,
                'answer': answer,
            })
            # Keep max 200 entries per document to avoid memory bloat
            entries = entries[-200:]
            self._redis.setex(key, CACHE_TTL_SECONDS, json.dumps(entries))
        except Exception as e:
            logger.error(f"Cache store error: {e}")

    def invalidate_document(self, document_id: int):
        """Invalidate all cache entries for a document (call on doc update)."""
        if not self._available:
            return
        try:
            pattern = f"qa_cache:{document_id}:*"
            keys = self._redis.keys(pattern)
            if keys:
                self._redis.delete(*keys)
                logger.info(f"Invalidated {len(keys)} cache keys for document {document_id}")
        except Exception as e:
            logger.error(f"Cache invalidation error: {e}")
