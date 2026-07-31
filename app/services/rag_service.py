"""
RAG (Retrieval-Augmented Generation) Service
Semantic chunk retrieval + Gemini-powered answer synthesis + token usage logging.
"""
import logging
import math
import time
from sqlalchemy.orm import Session

from app.clients.embedding_client import EmbeddingClient
from app.models.chunk import Chunk

logger = logging.getLogger(__name__)

# Minimum cosine similarity for a retrieved chunk to be considered on-topic.
# Below this threshold the question is treated as out-of-scope for the document.
RELEVANCE_THRESHOLD = 0.35

OUT_OF_SCOPE_REPLY = (
    "\u26a0\ufe0f This question appears to be outside the scope of the assigned document. "
    "Please refer to the SOP document directly, or ask a question that is specifically "
    "covered by the material in this document."
)


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
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def _estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Gemini 2.5 Flash pricing: $0.075/1M input, $0.30/1M output."""
    return (prompt_tokens * 0.075 + completion_tokens * 0.30) / 1_000_000


def _log_tokens_async(user_id: int, operation: str, prompt_tokens: int, completion_tokens: int, cost: float, latency_ms: int, cache_hit: bool = False):
    """Fire-and-forget token logging via Celery task (non-blocking)."""
    try:
        from app.tasks.report_tasks import log_token_usage
        log_token_usage.delay(user_id, operation, prompt_tokens, completion_tokens, cost, cache_hit, latency_ms)
    except Exception as e:
        logger.warning(f'Token log dispatch failed (non-critical): {e}')


class RagService:
    def __init__(self, db: Session, user_id: int = 0):
        self.db = db
        self.user_id = user_id  # For token logging attribution
        self.embedding_client = EmbeddingClient()

    def retrieve_chunks_hybrid(
        self, document_id: int, query: str, top_k: int = 3, rrf_k: int = 60
    ) -> list[tuple[float, "Chunk"]]:
        """
        Hybrid Retrieval combining BM25 Lexical Search & Dense Vector Cosine Similarity
        using Reciprocal Rank Fusion (RRF).
        """
        chunks: list[Chunk] = (
            self.db.query(Chunk)
            .filter(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
            .all()
        )
        if not chunks:
            return []

        # 1. Dense Vector Search Ranking
        query_vec = self.embedding_client.embed_text(query)
        vector_scored = []
        for chunk in chunks:
            sim = _cosine_similarity(query_vec, chunk.embedding) if chunk.embedding else 0.0
            vector_scored.append((sim, chunk))
        vector_scored.sort(key=lambda t: t[0], reverse=True)

        # 2. BM25 Lexical Keyword Ranking
        tokenized_corpus = [chunk.content.lower().split() for chunk in chunks]
        tokenized_query = query.lower().split()
        
        try:
            from rank_bm25 import BM25Okapi
            bm25 = BM25Okapi(tokenized_corpus)
            bm25_scores = bm25.get_scores(tokenized_query)
        except Exception as e:
            logger.warning(f"BM25 initialization failed, falling back to vector rank: {e}")
            bm25_scores = [0.0] * len(chunks)

        bm25_scored = list(zip(bm25_scores, chunks))
        bm25_scored.sort(key=lambda t: t[0], reverse=True)

        # 3. Compute Reciprocal Rank Fusion (RRF) Scores
        # RRF_score(d) = 1 / (rrf_k + rank_vector(d)) + 1 / (rrf_k + rank_bm25(d))
        rrf_scores: dict[int, float] = {}
        chunk_map: dict[int, Chunk] = {}
        vector_scored_dict = {c.id: s for s, c in vector_scored}

        for rank, (score, chunk) in enumerate(vector_scored):
            chunk_map[chunk.id] = chunk
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + (1.0 / (rrf_k + (rank + 1)))

        for rank, (score, chunk) in enumerate(bm25_scored):
            chunk_map[chunk.id] = chunk
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + (1.0 / (rrf_k + (rank + 1)))

        # Sort combined candidate list by RRF score descending
        hybrid_ranked = [
            (rrf_scores[cid], chunk_map[cid], vector_scored_dict.get(cid, 0.0))
            for cid in sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)
        ]
        
        return [(v_score, chunk) for rrf_s, chunk, v_score in hybrid_ranked[:top_k]]

    def retrieve_chunks_scored(
        self, document_id: int, query: str, top_k: int = 3
    ) -> list[tuple[float, "Chunk"]]:
        return self.retrieve_chunks_hybrid(document_id, query, top_k)

    def retrieve_chunks(self, document_id: int, query: str, top_k: int = 3) -> list["Chunk"]:
        """Return the top_k most relevant chunks using Hybrid RAG (BM25 + Vector + RRF)."""
        scored = self.retrieve_chunks_scored(document_id, query, top_k)
        if not scored:
            return []
        return [chunk for score, chunk in scored if score >= RELEVANCE_THRESHOLD]

    def is_query_in_scope(
        self, document_id: int, query: str, top_k: int = 3
    ) -> tuple[bool, list["Chunk"]]:
        """Check whether a query is in scope for the document using Hybrid RAG."""
        scored = self.retrieve_chunks_scored(document_id, query, top_k)
        if not scored:
            return False, []
        best_score = scored[0][0]
        if best_score < RELEVANCE_THRESHOLD:
            logger.info(
                f"Out-of-scope query for doc {document_id}: best relevance={best_score:.4f} < {RELEVANCE_THRESHOLD}"
            )
            return False, []
        relevant = [chunk for score, chunk in scored if score >= RELEVANCE_THRESHOLD]
        return True, relevant

    def generate_answer(self, query: str, context_chunks: list["Chunk"], cache_hit: bool = False) -> str:
        """Use Gemini to synthesise an answer grounded in the retrieved chunks."""
        from app.core.config import settings

        context_text = "\n\n---\n\n".join(
            f"[Page {c.page_no}, Chunk {c.chunk_index}]\n{c.content}"
            for c in context_chunks
        )

        if not settings.gemini_api_key or settings.gemini_api_key in ("change-me", "replace-me"):
            # Provide structured excerpt summary directly when API key is unconfigured
            sources_summary = "\n".join([f"• Page {c.page_no} (Chunk {c.chunk_index}): {c.content[:220]}..." for c in context_chunks])
            return f"Relevant SOP Document Excerpts:\n\n{sources_summary}\n\n(Configure GEMINI_API_KEY in Backend .env for full AI synthesis)."

        from google import genai as genai_sdk
        client = genai_sdk.Client(api_key=settings.gemini_api_key)

        prompt = f"""You are an expert pharmaceutical plant SOP & technical training assistant.
Answer the user's question clearly, thoroughly, and accurately based on the provided document excerpts.

GUIDELINES FOR YOUR ANSWER:
1. If the user asks for a summary or to "explain each one by one", provide a clear, structured, step-by-step breakdown explaining EVERY topic or concept mentioned in the excerpts individually.
2. Always cite page numbers and chunk indexes (e.g. [Page X, Chunk Y]).
3. Be comprehensive and educational — do NOT refuse broad requests like "explain each one by one". Synthesize all the provided material into an organized explanation.

DOCUMENT EXCERPTS:
{context_text}

QUESTION: {query}

ANSWER:"""

        t0 = time.monotonic()
        try:
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            answer = response.text.strip()
            latency_ms = int((time.monotonic() - t0) * 1000)

            p_tokens = _estimate_tokens(prompt)
            c_tokens = _estimate_tokens(answer)
            _log_tokens_async(
                self.user_id, 'rag_qa', p_tokens, c_tokens,
                _estimate_cost(p_tokens, c_tokens), latency_ms, cache_hit,
            )
            return answer
        except Exception as e:
            logger.error(f"RAG answer generation failed: {e}")
            sources_summary = "\n".join([f"• Page {c.page_no}: {c.content[:200]}..." for c in context_chunks])
            return f"Excerpts retrieved from document:\n{sources_summary}"

    def generate_re_explanation(self, chunk: Chunk, wrong_option: str, correct_option: str) -> str:
        """Re-explain a chunk concept when a candidate answers a question wrong."""
        from app.core.config import settings

        if not settings.gemini_api_key or settings.gemini_api_key in ("change-me", "replace-me"):
            return f"Let's revisit this concept: {chunk.content[:400]}"

        from google import genai as genai_sdk
        client = genai_sdk.Client(api_key=settings.gemini_api_key)

        prompt = f"""A candidate incorrectly chose option "{wrong_option}" instead of the correct answer "{correct_option}" for a question about this SOP content.

SOP CONTENT (Page {chunk.page_no}):
{chunk.content}

Please re-explain the key concept from this section clearly and engagingly, as if you are a patient pharmaceutical trainer.
Focus on WHY the correct answer "{correct_option}" is right and clarify any common misconceptions.
Keep your explanation to 3-5 sentences. Do NOT repeat the question or options — just explain the underlying concept."""

        t0 = time.monotonic()
        try:
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            answer = response.text.strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            p_tokens = _estimate_tokens(prompt)
            c_tokens = _estimate_tokens(answer)
            _log_tokens_async(
                self.user_id, 're_explanation', p_tokens, c_tokens,
                _estimate_cost(p_tokens, c_tokens), latency_ms,
            )
            return answer
        except Exception as e:
            logger.error(f"Re-explanation generation failed: {e}")
            return f"Let's revisit this concept from Page {chunk.page_no}: {chunk.content[:400]}"
