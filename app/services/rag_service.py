"""
RAG (Retrieval-Augmented Generation) Service
Semantic chunk retrieval + Gemini-powered answer synthesis + token usage logging.
"""
import logging
import math
import time
from collections import OrderedDict
from sqlalchemy.orm import Session, defer
from pgvector.sqlalchemy import Vector

from app.clients.embedding_client import EmbeddingClient
from app.models.chunk import Chunk
from app.models.parent_chunk import ParentChunk
from app.utils.constants import EMBEDDING_DIM  # E-3: single source of truth

logger = logging.getLogger(__name__)

# Minimum cosine similarity for a retrieved chunk to be considered on-topic.
# Below this threshold the question is treated as out-of-scope for the document.
# NOTE (R-1): the score applied here is the BLENDED score (0.6·cos + 0.4·bm25_norm).
# Recalibrate against a golden Q/A set before relying on this exact number.
RELEVANCE_THRESHOLD = 0.35

# R-2: minimum RAW cosine similarity a VECTOR-POOL chunk must have to be a
# valid candidate. Pure-lexical hits (cos ≈ 0) can no longer cross the blended
# threshold spuriously.
MIN_COSINE_FLOOR = 0.15

# P2 #6: per-structure-type overrides. documents.structure_type is classified
# at ingest ('structured' | 'unstructured'); legacy/None → 'unknown'. The
# unstructured variants are relaxed because blind token-batching produces
# fuzzier section boundaries whose blended scores sit systematically lower.
# Fallback to the globals above keeps docs without a DB-backed classification
# (test doubles, synthetic evals) on the exact pre-#6 behavior.
def _structure_thresholds(target_doc) -> tuple[float, float]:
    """(relevance_threshold, cosine_floor) for the document's structure type."""
    from app.utils.constants import (
        MIN_COSINE_FLOOR_BY_TYPE,
        RELEVANCE_THRESHOLD_BY_TYPE,
        STRUCTURE_STRUCTURED,
        STRUCTURE_UNKNOWN,
        STRUCTURE_UNSTRUCTURED,
    )
    s_type = getattr(target_doc, 'structure_type', None) if target_doc else None
    if s_type not in (STRUCTURE_STRUCTURED, STRUCTURE_UNSTRUCTURED):
        s_type = STRUCTURE_UNKNOWN
    return (
        RELEVANCE_THRESHOLD_BY_TYPE.get(s_type, RELEVANCE_THRESHOLD),
        MIN_COSINE_FLOOR_BY_TYPE.get(s_type, MIN_COSINE_FLOOR),
    )

# R-2: minimum normalised BM25 score for a chunk that was rescued by the
# full-corpus lexical pass (not present in the vector pool). This keeps the
# R-2 rescue alive while still filtering noise.
LEXICAL_RESCUE_FLOOR = 0.5

OUT_OF_SCOPE_REPLY = (
    "\u26a0\ufe0f This question appears to be outside the scope of the assigned document. "
    "Please refer to the SOP document directly, or ask a question that is specifically "
    "covered by the material in this document."
)

# E-1: reply when the embedding service is unavailable at query time.
EMBEDDING_UNAVAILABLE_REPLY = (
    "\u26a0\ufe0f The answer service is temporarily unavailable (embeddings could not be "
    "generated). Please try again in a few minutes."
)


# P0 #4: process-local BM25 index cache keyed by (document_id, doc_version).
# Building BM25Okapi tokenizes the WHOLE document corpus on every QA call —
# O(corpus) per question. The index only changes when a document is
# re-ingested, so we keep it warm per (doc, version) and invalidate by
# version-bump at ingestion time (ingestion_service, BM25_INDEX_VERSION_BUMP).
# LRU-capped so long-running workers don't grow without bound.
_BM25_CACHE: dict[int, tuple[int, object]] = {}  # doc_id -> (version_key, BM25Okapi)
_BM25_CACHE_MAX = 20
_BM25_CACHE_MISSING = -1  # sentinel version for docs with NULL version


def _get_bm25_index(target_doc_id: int, doc_version: int | None, corpus_chunks: list["Chunk"]):
    """Return a cached BM25Okapi for (doc, version), rebuilding only on miss.

    Only the INDEX is cached — never the ORM objects — so no detached
    instances are pinned across sessions. BM25 scores are positional, which
    stays correct because the caller's corpus query is deterministic
    (ORDER BY chunk_index, id) and chunk content is immutable within a
    version. Eviction is naive-LRU (re-insert moves to end via dict order).
    """
    version_key = doc_version if doc_version is not None else _BM25_CACHE_MISSING
    cached = _BM25_CACHE.get(target_doc_id)
    if cached is not None and cached[0] == version_key:
        # Refresh LRU position
        _BM25_CACHE[target_doc_id] = _BM25_CACHE.pop(target_doc_id)
        return cached[1]

    from rank_bm25 import BM25Okapi
    # P2 #3: table chunks contribute serialized rows + caption to the corpus.
    tokenized_corpus = [_bm25_tokenize(_bm25_chunk_text(c)) for c in corpus_chunks]
    index = BM25Okapi(tokenized_corpus)

    if cached is not None:
        _BM25_CACHE.pop(target_doc_id, None)
    _BM25_CACHE[target_doc_id] = (version_key, index)
    while len(_BM25_CACHE) > _BM25_CACHE_MAX:
        _BM25_CACHE.pop(next(iter(_BM25_CACHE)))
    return index


def _bm25_chunk_text(chunk: "Chunk") -> str:
    """P2 #3 — Table Retrieval: the text a chunk contributes to the BM25 corpus.

    Table chunks (markdown pipes / mammoth HTML tables) tokenize over their
    serialized ``Header: value`` rows with the stored caption repeated as a
    boost — raw pipe soup carries almost no query-aligned terms. Prose chunks
    tokenize exactly as before. Deterministic within a (doc, version), so the
    cached-index positional guarantee is unaffected. Any parse hiccup falls
    back to plain content."""
    from app.utils.table_utils import (
        build_table_lexical_text,
        is_table_chunk,
        parse_table,
        serialize_table,
    )
    try:
        if is_table_chunk(chunk.content):
            serialized = serialize_table(parse_table(chunk.content))
            if serialized:
                caption = (getattr(chunk, 'contextual_header', None) or '').strip() or None
                boosted = build_table_lexical_text(serialized, caption)
                if boosted:
                    return boosted
    except Exception:
        pass
    return chunk.content


def invalidate_bm25_cache(document_id: int) -> None:
    """Drop the cached BM25 index for a document (call after re-ingestion)."""
    _BM25_CACHE.pop(document_id, None)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _estimate_tokens(text: str) -> int:
    """C-2/Q-2: shared token estimator (single source of truth)."""
    from app.utils.tokenizer import estimate_tokens
    return estimate_tokens(text)


# R-2: word-boundary tokenizer for BM25. ``content.lower().split()`` treated
# "line-clearance" and "(line" as distinct opaque tokens, so queries for
# "line clearance" missed chunks whose only mention was hyphenated/punctuated.
_BM25_TOKEN_RE = None

# Out-of-scope guard: function words carry no topical signal, but with
# max-normalised BM25 they can dominate scores on small corpora — an entirely
# unrelated question ("what is the recipe for chocolate cake?") scored 1.0
# bm25_norm purely from "what/is/the/for". Dropping them from BOTH query and
# corpus tokenization removes the false signal at its source.
_BM25_STOPWORDS = frozenset({
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'been', 'but', 'by', 'can',
    'did', 'do', 'does', 'for', 'from', 'had', 'has', 'have', 'how', 'i',
    'in', 'is', 'it', 'its', 'may', 'of', 'on', 'or', 'our', 's', 'shall',
    'should', 't', 'that', 'the', 'their', 'them', 'then', 'there', 'these',
    'they', 'this', 'to', 'was', 'we', 'were', 'what', 'when', 'where',
    'which', 'who', 'why', 'will', 'with', 'you', 'your',
})


def _bm25_tokenize(text: str) -> list[str]:
    global _BM25_TOKEN_RE
    if _BM25_TOKEN_RE is None:
        import re
        _BM25_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
    return [t for t in _BM25_TOKEN_RE.findall(text.lower())
            if t not in _BM25_STOPWORDS]


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Gemini 2.5 Flash pricing ($0.30/1M in, $2.50/1M out) — see tokenizer.estimate_cost."""
    from app.utils.tokenizer import estimate_cost
    return estimate_cost(prompt_tokens, completion_tokens)


def _langfuse_usage(response, prompt: str, text: str) -> dict:
    """Langfuse usage dict from Gemini's REAL usage_metadata (estimate fallback).

    Delegates to ``tokenizer.langfuse_usage_details`` — the single source of
    truth for the canonical ``input`` / ``output`` / ``input_cached_tokens`` keys.
    """
    from app.utils.tokenizer import langfuse_usage_details
    return langfuse_usage_details(response, prompt, text)


def _log_tokens_async(user_id: int, operation: str, prompt_tokens: int, completion_tokens: int, cost: float, latency_ms: int, cache_hit: bool = False):
    """Fire-and-forget token logging via Celery task (non-blocking)."""
    try:
        from app.tasks.report_tasks import log_token_usage
        log_token_usage.delay(user_id, operation, prompt_tokens, completion_tokens, cost, cache_hit, latency_ms)
    except Exception as e:
        logger.warning(f'Token log dispatch failed (non-critical): {e}')


class EmbeddingUnavailable(Exception):
    """Raised at query time when embedding the user's question fails.

    Distinct from embedding_client.EmbeddingError so retrieval callers can
    degrade gracefully (clear reply) instead of failing the QA request with a
    bare 500.
    """


# R-4: module-level singleton so the cross-encoder model is downloaded once per
# process instead of on every rerank call.
_RERANKER_MODEL = None


def _get_reranker():
    global _RERANKER_MODEL
    if _RERANKER_MODEL is None:
        from sentence_transformers import CrossEncoder
        _RERANKER_MODEL = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
    return _RERANKER_MODEL


def _rerank_enabled() -> bool:
    """R-3: global switch so reranking can be disabled without a redeploy
    (settings.retrieval_rerank_enabled)."""
    from app.core.config import settings
    return settings.retrieval_rerank_enabled

class RagService:
    def __init__(self, db: Session, user_id: int = 0):
        self.db = db
        self.user_id = user_id  # For token logging attribution
        self.embedding_client = EmbeddingClient()

    def retrieve_chunks_hybrid(
        self, document_id: int, query: str, top_k: int = 3, rrf_k: int = 60, historical: bool = False,
        rerank: bool | None = None,
    ) -> list[tuple[float, "Chunk"]]:
        """
        Hybrid Retrieval combining BM25 Lexical Search & Dense Vector Cosine Similarity
        using Reciprocal Rank Fusion (RRF), optionally re-ranked by a cross-encoder (R-3).

        R-2 fix: BM25 now runs over the FULL document corpus (all chunks) rather
        than only the chunks the vector search already returned, so lexically
        relevant chunks the dense pass dropped can still be rescued by RRF.

        ``rerank``: None → follow the settings.retrieval_rerank_enabled switch;
        True/False → force on/off for this call.
        """
        from app.clients.langfuse_client import langfuse_observation

        do_rerank = _rerank_enabled() if rerank is None else rerank
        with langfuse_observation(
            name='hybrid-retrieval',
            as_type='retriever',
            user_id=self.user_id or None,
            tags=['rag', 'retrieval'],
            metadata={
                'document_id': document_id, 'query': query[:500], 'top_k': top_k,
                'historical': historical, 'rerank': do_rerank,
            },
        ) as retrieval_obs:
            result = self._retrieve_chunks_hybrid_impl(
                document_id, query, top_k=top_k, rrf_k=rrf_k, historical=historical,
                rerank=do_rerank,
            )
            retrieval_obs.update(
                output={
                    'retrieved': [{'chunk_id': c.id, 'score': round(s, 4), 'page_no': c.page_no} for s, c in result[:top_k]],
                    'count': len(result),
                }
            )
            return result

    def _retrieve_chunks_hybrid_impl(
        self, document_id: int, query: str, top_k: int = 3, rrf_k: int = 60, historical: bool = False,
        rerank: bool = False,
    ) -> list[tuple[float, "Chunk"]]:
        """The actual hybrid retrieval logic (kept separate so the retriever
        observation can record its output)."""
        # Resolve target document ID based on whether we want historical or latest
        from app.models.document import Document
        target_doc_id = document_id
        if not historical:
            doc = self.db.query(Document).filter(Document.id == document_id).first()
            if doc:
                latest_doc = self.db.query(Document).filter(
                    Document.code == doc.code,
                    Document.is_latest == True
                ).first()
                if latest_doc:
                    target_doc_id = latest_doc.id

        # E-2: refuse to search vectors created under a different embedding model.
        # Compare against the version the CURRENT client would actually produce:
        # a hash-mode client (no API key) creates hash vectors, so its documents
        # carry HASH_EMBEDDING_VERSION and must be compared against the hash tag.
        # Comparing against the real-model version unconditionally made every
        # hash-mode document unsearchable (stored=hash-fallback-v1 vs
        # current=gemini-embedding-2-dim768 → guaranteed mismatch → []).
        target_doc = self.db.query(Document).filter(Document.id == target_doc_id).first()
        if target_doc and target_doc.embedding_model_version:
            # Resolve against the client's ACTUAL vector space — callable
            # per-instance overrides win (offline stubs / duck-typed fakes);
            # minimal fakes that only carry `use_real` fall back to the
            # canonical EmbeddingClient version tags.
            from app.clients.embedding_client import EmbeddingClient as _EC
            model_version, hash_version = _EC.resolve_versions(self.embedding_client)
            current_version = (
                model_version
                if getattr(self.embedding_client, 'use_real', False)
                else hash_version
            )
            if target_doc.embedding_model_version != current_version:
                logger.error(
                    'Embedding version mismatch for doc %d: stored=%s current=%s — '
                    're-ingest required before retrieval (E-2).',
                    target_doc_id, target_doc.embedding_model_version, current_version,
                )
                return []

        # P2 #6: thresholds follow the document's ingest-time structure type.
        relevance_threshold, cosine_floor = _structure_thresholds(target_doc)

        try:
            query_vec = self.embedding_client.embed_text(query)
        except Exception as e:
            # E-1: a query-time embedding outage must not 500 the QA endpoint —
            # surface it as retrieval-unavailable so callers can reply clearly.
            logger.error(f'Query embedding failed, retrieval unavailable: {e}')
            raise EmbeddingUnavailable() from e

        # 1. Dense Vector Search — DB-level cosine distance via pgvector
        vector_scored: list[tuple[float, Chunk]] = []
        try:
            from sqlalchemy import cast
            vector_results = (
                self.db.query(
                    Chunk,
                    Chunk.embedding.cosine_distance(cast(query_vec, Vector(EMBEDDING_DIM))).label("cos_dist"),
                )
                .filter(Chunk.document_id == target_doc_id)
                .order_by("cos_dist")
                .limit(top_k * 10)  # Fetch a broader candidate pool for RRF
                .all()
            )
            vector_scored = [(1.0 - dist, chunk) for chunk, dist in vector_results if dist is not None]
            vector_scored.sort(key=lambda t: t[0], reverse=True)
        except Exception as e:
            logger.warning(f"pgvector query failed, falling back to Python cosine: {e}")
            all_chunks: list[Chunk] = (
                self.db.query(Chunk).options(defer(Chunk.embedding))
                .filter(Chunk.document_id == target_doc_id)
                .order_by(Chunk.chunk_index)
                .all()
            )
            vector_scored = [
                # Score = raw cosine similarity (matches the pgvector branch's
                # ``1.0 - cosine_distance``) — NOT ``1 - cos``, which is a
                # distance and previously ranked the LEAST similar chunk first.
                (_cosine_similarity(query_vec, chunk.embedding) if chunk.embedding else 0.0, chunk)
                for chunk in all_chunks
            ]
            vector_scored = [t for t in sorted(vector_scored, key=lambda x: x[0], reverse=True)]

        if not vector_scored:
            return []

        # 2. BM25 over the FULL document corpus (R-2 fix) — index is cached
        # per (doc, version) so repeated questions skip tokenization entirely.
        corpus_chunks: list[Chunk] = (
            self.db.query(Chunk).options(defer(Chunk.embedding))
            .filter(Chunk.document_id == target_doc_id)
            .order_by(Chunk.chunk_index, Chunk.id)
            .all()
        )
        tokenized_query = _bm25_tokenize(query)

        bm25_scores: list[float] = []
        try:
            target_doc_version = target_doc.version if target_doc else None
            bm25 = _get_bm25_index(target_doc_id, target_doc_version, corpus_chunks)
            # rank_bm25 returns a NUMPY array — normalise to a plain list so the
            # truthiness checks below never hit numpy's ambiguous-value error.
            # (That error previously crashed every retrieve_chunks_hybrid call
            # whenever rank_bm25 was installed, turning every QA question into
            # an 'embeddings unavailable' reply.)
            bm25_scores = list(bm25.get_scores(tokenized_query))
        except Exception as e:
            logger.warning(f"BM25 initialization failed, falling back to vector rank: {e}")
            bm25_scores = [0.0] * len(corpus_chunks)

        # 3. RRF over the union of both candidate pools
        rrf_scores: dict[int, float] = {}
        chunk_map: dict[int, Chunk] = {}
        vector_scored_dict: dict[int, float] = {}
        vector_pool_ids: set[int] = set()
        for rank, (score, chunk) in enumerate(vector_scored):
            chunk_map[chunk.id] = chunk
            vector_scored_dict[chunk.id] = score
            vector_pool_ids.add(chunk.id)
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + (1.0 / (rrf_k + (rank + 1)))

        bm25_scored = sorted(
            ((s, c) for s, c in zip(bm25_scores, corpus_chunks) if s > 0.0),
            key=lambda t: t[0], reverse=True,
        )
        for rank, (score, chunk) in enumerate(bm25_scored):
            chunk_map[chunk.id] = chunk
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + (1.0 / (rrf_k + (rank + 1)))

        max_bm25 = max(bm25_scores) if len(bm25_scores) > 0 else 1.0
        max_bm25 = max_bm25 if max_bm25 > 0 else 1.0
        bm25_score_map: dict[int, float] = {c.id: s for s, c in zip(bm25_scores, corpus_chunks)}

        # 4. Blended score (60% vector + 40% normalised BM25) sorted by RRF.
        # R-1/R-2 reconciliation:
        #   - chunks from the VECTOR pool must clear the raw cosine floor so
        #     weakly-similar lexical-only matches can't spuriously pass;
        #   - EXCEPT when the lexical evidence is unambiguous: a pool chunk
        #     that is the corpus's best BM25 match (bm25_norm above
        #     LEXICAL_RESCUE_FLOOR) survives the floor. On small corpora the
        #     pool covers every chunk, so without this escape hatch the rescue
        #     rule below could never fire and a paraphrased-but-lexically-
        #     perfect chunk (cos 0.08, bm25_norm 1.0) was discarded.
        #   - chunks rescued by the FULL-CORPUS BM25 pass (not in the vector
        #     pool, v_score=0) are kept only when their lexical signal is strong
        #     (bm25_norm above LEXICAL_RESCUE_FLOOR) — otherwise the R-2 rescue
        #     would be defeated by the cosine floor.
        hybrid_ranked: list[tuple[float, Chunk]] = []
        for cid in sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True):
            chunk = chunk_map[cid]
            v_score = vector_scored_dict.get(cid, 0.0)
            bm25_norm = bm25_score_map.get(cid, 0.0) / max_bm25
            if cid in vector_pool_ids:
                if v_score < cosine_floor and bm25_norm < LEXICAL_RESCUE_FLOOR:
                    continue
            elif bm25_norm < LEXICAL_RESCUE_FLOOR:
                continue
            blended = 0.6 * v_score + 0.4 * bm25_norm
            hybrid_ranked.append((blended, chunk))

        # R-3: rerank the candidate pool with the cross-encoder before cutting
        # to top_k. The blended score is kept as a tiebreak base; the reranker
        # decides the final ORDER of the top candidates. Degradations handled
        # inside _rerank: library missing / inference error → original order.
        reranked = None
        if rerank and hybrid_ranked:
            rerank_pool = hybrid_ranked[: self._rerank_candidate_count()]
            reranked = self._rerank(query, [c for _, c in rerank_pool])
        if reranked:
            rerank_score_map = {c.id: s for s, c in reranked}
            hybrid_ranked = sorted(
                hybrid_ranked,
                key=lambda t: (
                    t[1].id in rerank_score_map,  # reranked candidates first
                    rerank_score_map.get(t[1].id, 0.0),  # by cross-encoder score
                    t[0],  # then by blended RRF score
                ),
                reverse=True,
            )

        return hybrid_ranked[:top_k]

    @staticmethod
    def _rerank_candidate_count() -> int:
        from app.core.config import settings
        return max(3, settings.retrieval_rerank_candidates)

    def retrieve_chunks_scored(
        self, document_id: int, query: str, top_k: int = 3, historical: bool = False, rerank: bool = False
    ) -> list[tuple[float, "Chunk"]]:
        """Reranking happens INSIDE the hybrid pipeline (on the wider RRF pool)
        when enabled; the ``rerank`` request flag can force it on for callers
        that explicitly asked, overriding the global disable switch."""
        force_rerank = rerank and not _rerank_enabled()
        scored = self.retrieve_chunks_hybrid(
            document_id, query, top_k, historical=historical,
            rerank=(rerank or _rerank_enabled()),
        )
        if force_rerank and scored:
            return self._rerank(query, [c for _, c in scored]) or scored
        return scored

    def retrieve_chunks(self, document_id: int, query: str, top_k: int = 3, historical: bool = False, rerank: bool = False) -> list["Chunk"]:
        """Return the top_k most relevant chunks using Hybrid RAG (BM25 + Vector + RRF).

        P2 #6: the blended-score gate follows the document's ingest-time
        structure type (relaxed for 'unstructured'); documents without a
        classification keep the global RELEVANCE_THRESHOLD."""
        scored = self.retrieve_chunks_scored(document_id, query, top_k, historical=historical, rerank=rerank)
        if not scored:
            return []
        relevance_threshold = self._relevance_threshold_for(document_id, historical=historical)
        # R-1: also require a minimum raw cosine floor so pure-lexical hits don't
        # pass the blended threshold spuriously.
        return [chunk for score, chunk in scored if score >= relevance_threshold]

    def retrieve_chunks_multi_doc(
        self, document_ids: list[int], query: str, top_k: int = 5
    ) -> list[tuple[float, "Chunk"]]:
        """G-10: Hybrid retrieval across multiple documents.
        Collects top_k candidates per document, then re-ranks globally and
        returns the best top_k across all documents.  Enables cross-SOP queries."""
        all_scored: list[tuple[float, "Chunk"]] = []
        for doc_id in document_ids:
            try:
                scored = self.retrieve_chunks_hybrid(doc_id, query, top_k=top_k)
                all_scored.extend(scored)
            except Exception as e:
                logger.warning(f"Multi-doc retrieval failed for doc {doc_id}: {e}")
        all_scored.sort(key=lambda t: t[0], reverse=True)
        return all_scored[:top_k]

    def is_query_in_scope(
        self, document_id: int, query: str, top_k: int = 3, historical: bool = False
    ) -> tuple[bool, list["Chunk"]]:
        """Check whether a query is in scope for the document using Hybrid RAG.

        R-8 fix: uses the AVERAGE of the top-k scores rather than only the best,
        so a query with one strong chunk and several weak ones is judged on the
        whole retrieval set.
        """
        scored = self.retrieve_chunks_scored(document_id, query, top_k, historical=historical)
        relevance_threshold = self._relevance_threshold_for(document_id, historical=historical)
        if not scored:
            return False, []
        avg_score = sum(s for s, _ in scored) / len(scored)
        if avg_score < relevance_threshold:
            logger.info(
                f"Out-of-scope query for doc {document_id}: avg relevance={avg_score:.4f} < {relevance_threshold:.2f} "
                f"(structure_type={getattr(self._structure_doc_for(document_id, historical), 'structure_type', None)})"
            )
            return False, []
        relevant = [chunk for score, chunk in scored if score >= relevance_threshold]
        return True, relevant

    def _structure_doc_for(self, document_id: int, historical: bool = False):
        """Fetch the target document row for structure-type threshold lookup.
        Returns None when missing — callers fall back to global defaults."""
        from app.models.document import Document
        target_doc_id = document_id
        if not historical:
            doc = self.db.query(Document).filter(Document.id == document_id).first()
            if doc:
                latest_doc = self.db.query(Document).filter(
                    Document.code == doc.code,
                    Document.is_latest == True
                ).first()
                if latest_doc:
                    target_doc_id = latest_doc.id
        return self.db.query(Document).filter(Document.id == target_doc_id).first()

    def _relevance_threshold_for(self, document_id: int, historical: bool = False) -> float:
        """P2 #6: relevance threshold for the document's structure type."""
        return _structure_thresholds(self._structure_doc_for(document_id, historical))[0]

    def _rerank(
        self, query: str, chunks: list["Chunk"]
    ) -> list[tuple[float, "Chunk"]] | None:
        """R-3/R-4: Optional cross-encoder reranking via sentence-transformers.
        Uses a module-level singleton so the model is downloaded once per process.
        Upgrade 2: reranks over contextual header + content when available.
        Returns None when the library is unavailable so callers keep the original
        RRF order (graceful degradation)."""
        try:
            model = _get_reranker()
            pairs = []
            for c in chunks:
                header = (getattr(c, 'contextual_header', None) or '').strip()
                text = f"{header}\n\n{c.content}"[:768] if header else c.content[:512]
                pairs.append((query, text))
            scores = model.predict(pairs)
            ranked = sorted(zip(scores.tolist(), chunks), key=lambda t: t[0], reverse=True)
            return [(float(s), c) for s, c in ranked]
        except ImportError:
            logger.debug("sentence-transformers not installed — cross-encoder reranking skipped.")
            return None
        except Exception as e:
            logger.warning(f"Cross-encoder reranking failed, skipping: {e}")
            return None

    # ── Small-to-big: section-context construction ─────────────────────────────

    @staticmethod
    def _build_parent_sections(
        context_chunks: list["Chunk"],
        top_sections: int = 3,
        per_section_child_budget: int = 12,
        max_context_tokens: int = 10000,
    ) -> list[dict]:
        """Group retrieved children by parent and group siblings into ordered sets.

        Chunk-level retrieval stays authoritative for SCORING (citations, scope
        checks, cache keys); the LLM context is rebuilt at SECTION granularity
        so answers synthesize from complete sections instead of 500-token
        fragments (small-to-big retrieval).

        Parameters:
            top_sections:      max parents whose sections enter the context
            per_section_child_budget: max children rendered per section, children
                               beyond the budget contribute title + page range
                               only (never silently dropped)
            max_context_tokens: total prompt budget; sections are added in
                               relevance order until the budget is exhausted
        """
        groups: "OrderedDict[int, dict]" = OrderedDict()
        for c in context_chunks:
            groups.setdefault(c.parent_chunk_id, []).append(c)

        sections = []
        for pid, kids in groups.items():
            siblings = sorted(kids, key=lambda k: k.child_index)
            sections.append({
                'parent_id': pid,
                'title': '',
                'summary': '',
                'page_start': min(k.page_no for k in siblings),
                'page_end': max(k.page_no for k in siblings),
                'retrieved_children': siblings,
                'has_parent_row': pid is not None,
                'all_children': [],
                'children_total': len(siblings),
            })
        return sections[:top_sections]

    def _fetch_section_context(self, sections: list[dict]) -> None:
        """In-place: fetch parent rows and all sibling children for each section.

        Children not in the retrieval hit-set are still rendered — they carry
        surrounding context for synthesis, but their page refs are NOT eligible
        as citations (they weren't retrieved).
        """
        parent_ids = [s['parent_id'] for s in sections if s['has_parent_row']]
        parents_by_id = {}
        if parent_ids:
            rows = self.db.query(ParentChunk).filter(ParentChunk.id.in_(parent_ids)).all()
            parents_by_id = {p.id: p for p in rows}

        for s in sections:
            if not s['has_parent_row']:  # synthetic/no-parent path
                continue
            p = parents_by_id.get(s['parent_id'])
            if p is None:
                s['has_parent_row'] = False
                continue
            s['title'] = p.title or f"Section {p.section_index}"
            s['summary'] = p.summary or ''
            s['page_start'] = p.page_start
            s['page_end'] = p.page_end
            siblings = (
                self.db.query(Chunk)
                .filter(Chunk.parent_chunk_id == s['parent_id'])
                .order_by(Chunk.child_index)
                .all()
            )
            s['all_children'] = siblings
            s['children_total'] = len(siblings)

    @staticmethod
    def _render_section_context(sections: list[dict], max_context_tokens: int = 10000) -> str:
        """Render grouped sections as the LLM context, under a token budget.

        Per section: title/summary header, page range, and every child with its
        own [Page X, Chunk Y] tag so the model can cite precisely. Children NOT
        in the retrieval hit-set are tagged with their real page but marked
        ``ctx`` (context-only) so the prompt never invites citing unretrieved
        chunks. Tables and lists stay intact inside their child blocks.
        """
        from app.utils.text_utils import strip_preceding_context
        from app.utils.tokenizer import estimate_tokens

        retrieved_ids = {
            c.id for s in sections for c in s.get('retrieved_children', [])
        }

        blocks: list[str] = []
        used = 0
        for s in sections:
            if not s.get('has_parent_row'):
                # Synthetic parents (legacy parent-less docs): render retrieved
                # children directly, same tags as before.
                lines = []
                for c in s['retrieved_children']:
                    lines.append(
                        f"[Page {c.page_no}, Chunk {c.chunk_index}]\n"
                        + (f"({getattr(c, 'contextual_header', None)})\n" if getattr(c, 'contextual_header', None) else '')
                        + strip_preceding_context(c.content)
                    )
                block = '\n\n'.join(lines)
            else:
                header = f"SECTION: {s['title']}"
                if s.get('summary'):
                    header += f" — {s['summary'][:200]}"
                header += f" (Pages {s['page_start']}–{s['page_end']})"
                lines = [header]
                kids = s.get('all_children') or s['retrieved_children']
                overflow = []
                for c in kids:
                    tag = f"[Page {c.page_no}, Chunk {c.chunk_index}]"
                    if c.id not in retrieved_ids:
                        tag += ' (ctx)'
                    text = strip_preceding_context(c.content)
                    if len(lines) - 1 >= 12 and c.id not in retrieved_ids:
                        # Beyond the per-section child budget: context-only
                        # children collapse to a one-line index entry.
                        overflow.append(f"{tag} {s['title']} — continued (use only if needed)")
                        continue
                    lines.append(f"{tag}\n{text}")
                if overflow:
                    lines.append('Also in this section:')
                    lines.extend(overflow)
                block = '\n\n'.join(lines)

            cost = estimate_tokens(block)
            if blocks and used + cost > max_context_tokens:
                break
            blocks.append(block)
            used += cost

        return '\n\n---\n\n'.join(blocks)

    def generate_answer(self, query: str, context_chunks: list["Chunk"], cache_hit: bool = False) -> str:
        """Use Gemini to synthesise an answer grounded in the retrieved chunks.

        Small-to-big: children are grouped into their parent sections and the
        prompt receives COMPLETE sections (all siblings, section titles and
        page ranges) — while citations stay child-granular via the
        [Page X, Chunk Y] tags. Sections are ordered by retrieval relevance and
        trimmed to a token budget.
        """
        sections = self._build_parent_sections(context_chunks)
        self._fetch_section_context(sections)
        context_text = self._render_section_context(sections)

        retrieved_tags = {
            f"[Page {c.page_no}, Chunk {c.chunk_index}]" for c in context_chunks
        }
        from app.core.config import settings
        from google.genai import types
        from app.utils.text_utils import strip_preceding_context

        # Q-1 fix: never feed the [Preceding Section: ...] recap to the LLM — it
        # leaks previous-section text into the answer while the citation points
        # at the current chunk/page.
        # (Context is now rendered from grouped SECTIONS above — see
        # _render_section_context; ``strip_preceding_context`` is applied there.)
        from app.clients.langfuse_client import langfuse_observation

        if not settings.gemini_api_key or settings.gemini_api_key in ("change-me", "replace-me"):
            # Provide structured excerpt summary directly when API key is unconfigured
            sources_summary = "\n".join([f"• Page {c.page_no} (Chunk {c.chunk_index}): {c.content[:220]}..." for c in context_chunks])
            return f"Relevant SOP Document Excerpts:\n\n{sources_summary}\n\n(Configure GEMINI_API_KEY in Backend .env for full AI synthesis)."

        from google import genai as genai_sdk
        client = genai_sdk.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=30_000),
        )

        # Dynamic temperature and system instruction tuning based on document type
        temperature = 0.0
        system_instruction = "You are an expert pharmaceutical plant SOP & technical training assistant."

        if context_chunks:
            try:
                from app.models.document import Document
                doc_id = context_chunks[0].document_id
                doc = self.db.query(Document).filter(Document.id == doc_id).first()
                if doc:
                    is_sop = "sop" in doc.code.lower() or doc.qa_scope == "doc_strict"
                    if not is_sop:
                        temperature = 0.3
                        system_instruction = "You are an encouraging and educational plant training assistant explaining technical concepts."
            except Exception as e:
                logger.warning(f"Failed to query document type for RAG tuning: {e}")

        prompt = f"""Answer the user's question clearly, thoroughly, and accurately based ONLY on the provided document sections.
If the answer cannot be determined or inferred from the provided sections, state clearly: "I cannot find the answer to this question in the provided document sections." Do NOT attempt to answer using pre-trained general knowledge.

CONTEXT FORMAT:
- The context is organized into complete document SECTIONS, each introduced by a "SECTION:" header with a page range.
- Each excerpt is tagged [Page X, Chunk Y]. Tags marked "(ctx)" are surrounding context within the same section — prefer citing NON-(ctx) tags, which match what was actually retrieved for this question.

GUIDELINES FOR YOUR ANSWER:
1. If the user asks for a summary or to "explain each one by one", provide a clear, structured, step-by-step breakdown explaining EVERY topic or concept mentioned in the sections individually.
2. Always cite page numbers and chunk indexes (e.g. [Page X, Chunk Y]). Prefer non-(ctx) tags.
3. Be comprehensive and educational — do NOT refuse broad requests like "explain each one by one". Synthesize all the provided material into an organized explanation. A question may span several excerpts within one section — synthesize across them.

DOCUMENT SECTIONS:
{context_text}

QUESTION: {query}

ANSWER:"""

        t0 = time.monotonic()
        try:
            with langfuse_observation(
                name='rag-answer-generation',
                as_type='generation',
                user_id=self.user_id or None,
                tags=['rag', 'generation'],
                metadata={'operation': 'rag_qa', 'cache_hit': cache_hit, 'n_chunks': len(context_chunks)},
                model='gemini-2.5-flash',
                input_data={'prompt': prompt[:8000]},
            ) as gen:
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=temperature
                    )
                )
                answer = response.text.strip()
                latency_ms = int((time.monotonic() - t0) * 1000)

                usage = _langfuse_usage(response, prompt, answer)
                p_tokens = (usage.get('input') or 0) + (usage.get('input_cached_tokens') or 0)
                c_tokens = usage.get('output') or 0
                _log_tokens_async(
                    self.user_id, 'rag_qa', p_tokens, c_tokens,
                    _estimate_cost(p_tokens, c_tokens), latency_ms, cache_hit,
                )
                gen.update(output=answer[:6000], usage_details=usage)

            # Q-3: post-generation grounding check — when context was provided,
            # the answer should cite at least one [Page X, Chunk Y]. If not, log
            # the violation and append the source list so citations always exist.
            # Small-to-big: appended sources prefer the SECTION header form for
            # multi-chunk sections; child tags are kept for precision.
            import re as _re
            has_citation = bool(_re.search(r'\[Page \d+, Chunk \d+\]', answer))
            if sections and not has_citation:
                logger.warning(
                    'Answer for query %r contains no [Page X, Chunk Y] citation — appending sources.',
                    query[:80],
                )
                sources = ', '.join(
                    f"[{s['title']} (Pages {s['page_start']}–{s['page_end']})]"
                    for s in sections
                )
                answer = f"{answer}\n\n**Sources:** {sources}"
            return answer
        except Exception as e:
            logger.error(f"RAG answer generation failed: {e}")
            # Q-5: signal clearly that this is NOT a synthesised answer.
            sources_summary = "\n".join([f"• Page {c.page_no}: {c.content[:200]}..." for c in context_chunks])
            return f"⚠️ AI answer synthesis failed. Excerpts retrieved from document:\n{sources_summary}"

    def generate_re_explanation(self, chunk: Chunk, wrong_option: str, correct_option: str) -> str:
        """Re-explain a chunk concept when a candidate answers a question wrong."""
        from app.core.config import settings

        if not settings.gemini_api_key or settings.gemini_api_key in ("change-me", "replace-me"):
            return f"Let's revisit this concept: {chunk.content[:400]}"

        from google import genai as genai_sdk
        from google.genai import types
        client = genai_sdk.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=30_000),
        )

        prompt = f"""A candidate incorrectly chose option "{wrong_option}" instead of the correct answer "{correct_option}" for a question about this SOP content.

SOP CONTENT (Page {chunk.page_no}):
{chunk.content}

Please re-explain the key concept from this section clearly and engagingly, as if you are a patient pharmaceutical trainer.
Focus on WHY the correct answer "{correct_option}" is right and clarify any common misconceptions.
Keep your explanation to 3-5 sentences. Do NOT repeat the question or options — just explain the underlying concept."""

        from app.clients.langfuse_client import langfuse_observation

        t0 = time.monotonic()
        try:
            with langfuse_observation(
                name='re-explain-concept',
                as_type='generation',
                user_id=self.user_id or None,
                tags=['rag', 'generation'],
                metadata={'operation': 're_explanation', 'page_no': chunk.page_no},
                model='gemini-2.5-flash',
                input_data={'prompt': prompt[:8000]},
            ) as gen:
                response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
                answer = response.text.strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = _langfuse_usage(response, prompt, answer)
                p_tokens = (usage.get('input') or 0) + (usage.get('input_cached_tokens') or 0)
                c_tokens = usage.get('output') or 0
                _log_tokens_async(
                    self.user_id, 're_explanation', p_tokens, c_tokens,
                    _estimate_cost(p_tokens, c_tokens), latency_ms,
                )
                gen.update(output=answer[:6000], usage_details=usage)
            return answer
        except Exception as e:
            logger.error(f"Re-explanation generation failed: {e}")
            return f"Let's revisit this concept from Page {chunk.page_no}: {chunk.content[:400]}"
