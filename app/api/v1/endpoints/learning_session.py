"""
Adaptive Learning Session Endpoints — Phase 3 Complete

GET  /learning/session/document/{document_id}/start   → Load chunks + resume or start from chunk 1
GET  /learning/session/document/{document_id}/resume  → Return the last saved chunk position
GET  /learning/session/chunk/{chunk_id}               → Get a specific chunk + MCQ
POST /learning/session/chunk/{chunk_id}/answer        → Submit answer; persist progress
POST /learning/session/qa                             → RAG Q&A scoped to document
POST /learning/session/chunk/{chunk_id}/understood    → Mark chunk as understood (no MCQ path)
"""
import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.learning_service import LearningService
from app.services.learning_session_service import LearningSessionService
from app.services.rag_service import RagService

router = APIRouter(prefix='/learning', tags=['learning-session'])

logger = logging.getLogger(__name__)


def _check_document_access(db: Session, user: User, document_id: int):
    # Access guard: trainees must have an assignment for this document
    if user.role == UserRole.trainee:
        from app.models.training import TrainingAssignment
        assignment = db.query(TrainingAssignment).filter(
            TrainingAssignment.user_id == user.id,
            TrainingAssignment.document_id == document_id,
        ).first()
        if not assignment:
            raise HTTPException(
                status_code=403,
                detail='You do not have an assignment for this document. Contact your HOD or trainer.',
            )

        # Check sequential unlocking within the topic
        from app.models.document import Document
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc and doc.topic_id:
            # Find any active documents in the same topic with a lower sequence_order
            # Or if sequence_order is equal, a lower document ID
            previous_docs = db.query(Document).filter(
                Document.topic_id == doc.topic_id,
                Document.status == 'active',
                (Document.sequence_order < doc.sequence_order) | 
                ((Document.sequence_order == doc.sequence_order) & (Document.id < doc.id))
            ).all()
            
            for prev_doc in previous_docs:
                completed_assignment = db.query(TrainingAssignment).filter(
                    TrainingAssignment.user_id == user.id,
                    TrainingAssignment.document_id == prev_doc.id,
                    TrainingAssignment.status == 'completed'
                ).first()
                if not completed_assignment:
                    raise HTTPException(
                        status_code=403,
                        detail=f"This document is locked. You must first complete training for: {prev_doc.title}"
                    )


def get_session_service(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return LearningSessionService(db, user_id=current_user.id)


def get_rag_service(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return RagService(db, user_id=current_user.id)



# ─── Schemas ─────────────────────────────────────────────────────────────────

class ChunkOut(BaseModel):
    id: int
    document_id: int
    chunk_index: int
    page_no: int
    content: str
    learning_card: str | None
    token_count: int
    model_config = {'from_attributes': True}


class McqOut(BaseModel):
    id: int
    question: str
    options: dict
    difficulty: str
    type: str
    model_config = {'from_attributes': True}


class SessionStartResponse(BaseModel):
    total_chunks: int
    chunks: list[ChunkOut]
    first_chunk: ChunkOut
    first_mcq: McqOut | None
    resume_chunk_id: int | None
    completion_percentage: float
    review_mode_unlocked: bool
    max_unlocked_chunk_index: int


class ChunkDetailResponse(BaseModel):
    chunk: ChunkOut
    mcq: McqOut | None


class AnswerRequest(BaseModel):
    mcq_id: int
    selected_option: str
    completed_chunk_ids: list[int] = []   # Client sends accumulated set for progress calc
    time_spent_seconds: int = 0


class AnswerResponse(BaseModel):
    is_correct: bool
    correct_option: str
    explanation: str
    re_explanation: str | None
    retry_mcq: McqOut | None
    next_chunk: ChunkOut | None
    next_chunk_mcq: McqOut | None
    progress_percentage: float


class QARequest(BaseModel):
    document_id: int
    question: str
    topic_id: int | None = None
    rerank: bool = False  # R-3: opt-in cross-encoder reranking


class QAResponse(BaseModel):
    question: str
    answer: str
    source_chunks: list[ChunkOut]


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get('/session/document/{document_id}/start', response_model=SessionStartResponse)
def start_session(
    document_id: int,
    service: LearningSessionService = Depends(get_session_service),
    current_user: User = Depends(get_current_user),
):
    """Load all chunks for a document; auto-resume to last saved chunk."""
    _check_document_access(service.db, current_user, document_id)

    chunks = service.get_chunks(document_id)
    if not chunks:
        raise HTTPException(status_code=404, detail='No chunks found. Ensure the document has been ingested.')

    # Check for saved progress (auto-resume)
    resume_chunk = service.get_resume_position(current_user.id, document_id)
    first = resume_chunk if resume_chunk else chunks[0]

    mcq = service.get_mcq_for_chunk(first.id)
    if not mcq:
        mcq = service.generate_mcq_on_demand(first)

    from app.models.user_progress import UserProgress
    progress = service.db.query(UserProgress).filter(
        UserProgress.user_id == current_user.id,
        UserProgress.document_id == document_id,
    ).first()
    completion_pct = progress.completion_percentage if progress else 0.0

    learning_svc = LearningService(service.db)
    review_unlocked = learning_svc.is_first_pass_complete(current_user.id, document_id)
    max_unlocked_idx = learning_svc.get_max_unlocked_chunk_index(current_user.id, document_id)

    return {
        'total_chunks': len(chunks),
        'chunks': chunks,
        'first_chunk': first,
        'first_mcq': mcq,
        'resume_chunk_id': resume_chunk.id if resume_chunk else None,
        'completion_percentage': completion_pct,
        'review_mode_unlocked': review_unlocked,
        'max_unlocked_chunk_index': max_unlocked_idx,
    }


@router.get('/session/chunk/{chunk_id}', response_model=ChunkDetailResponse)
def get_chunk_detail(
    chunk_id: int,
    service: LearningSessionService = Depends(get_session_service),
    current_user: User = Depends(get_current_user),
):
    chunk = service.get_chunk(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail='Chunk not found')

    _check_document_access(service.db, current_user, chunk.document_id)

    mcq = service.get_mcq_for_chunk(chunk.id)
    if not mcq:
        mcq = service.generate_mcq_on_demand(chunk)

    return {'chunk': chunk, 'mcq': mcq}


@router.post('/session/chunk/{chunk_id}/answer', response_model=AnswerResponse)
def submit_answer(
    chunk_id: int,
    payload: AnswerRequest,
    service: LearningSessionService = Depends(get_session_service),
    current_user: User = Depends(get_current_user),
):
    """
    Submit an MCQ answer for a chunk.
    - Correct → persist progress + return next chunk + MCQ
    - Wrong   → return RAG re-explanation + fresh retry MCQ
    """
    current_chunk = service.get_chunk(chunk_id)
    if not current_chunk:
        raise HTTPException(status_code=404, detail='Chunk not found')

    _check_document_access(service.db, current_user, current_chunk.document_id)

    try:
        evaluation = service.evaluate_answer(chunk_id, payload.mcq_id, payload.selected_option)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    all_chunks = service.get_chunks(current_chunk.document_id)
    total_chunks = len(all_chunks)

    # Build completed set — include current chunk if correct
    completed_ids = set(payload.completed_chunk_ids)
    if evaluation['is_correct']:
        completed_ids.add(chunk_id)

    # Persist progress to DB
    service.save_progress(
        user_id=current_user.id,
        document_id=current_chunk.document_id,
        current_chunk=current_chunk,
        completed_chunk_ids=list(completed_ids),
        total_chunks=total_chunks,
        time_spent_seconds=payload.time_spent_seconds,
    )

    progress_pct = (len(completed_ids) / total_chunks * 100) if total_chunks > 0 else 0.0

    response: dict = {
        'is_correct': evaluation['is_correct'],
        'correct_option': evaluation['correct_option'],
        'explanation': evaluation['explanation'],
        're_explanation': evaluation['re_explanation'],
        'retry_mcq': None,
        'next_chunk': None,
        'next_chunk_mcq': None,
        'progress_percentage': round(progress_pct, 2),
    }

    if evaluation['is_correct']:
        current_idx = next((i for i, c in enumerate(all_chunks) if c.id == chunk_id), None)
        if current_idx is not None and current_idx + 1 < total_chunks:
            next_chunk = all_chunks[current_idx + 1]
            next_mcq = service.get_mcq_for_chunk(next_chunk.id)
            if not next_mcq:
                next_mcq = service.generate_mcq_on_demand(next_chunk)
            response['next_chunk'] = next_chunk
            response['next_chunk_mcq'] = next_mcq
    else:
        retry_mcq = service.get_fresh_mcq_for_chunk(chunk_id, exclude_mcq_id=payload.mcq_id)
        if not retry_mcq:
            retry_mcq = service.generate_mcq_on_demand(current_chunk)
        response['retry_mcq'] = retry_mcq

    return response


@router.post('/session/chunk/{chunk_id}/understood', status_code=200)
def mark_understood(
    chunk_id: int,
    payload: dict | None = None,  # A-6: no mutable default
    service: LearningSessionService = Depends(get_session_service),
    current_user: User = Depends(get_current_user),
):
    """Mark a chunk as understood without an MCQ (review mode shortcut)."""
    payload = payload or {}
    chunk = service.get_chunk(chunk_id)
    if not chunk:
        raise HTTPException(status_code=404, detail='Chunk not found')

    _check_document_access(service.db, current_user, chunk.document_id)

    all_chunks = service.get_chunks(chunk.document_id)
    completed_ids = payload.get('completed_chunk_ids', [chunk_id])
    service.save_progress(
        user_id=current_user.id,
        document_id=chunk.document_id,
        current_chunk=chunk,
        completed_chunk_ids=completed_ids,
        total_chunks=len(all_chunks),
        time_spent_seconds=payload.get('time_spent_seconds', 0),
    )
    return {'status': 'understood', 'chunk_id': chunk_id}


@router.post('/chunks/{chunk_id}/understood', status_code=200)
def mark_understood_alias(
    chunk_id: int,
    payload: dict | None = None,  # A-6: no mutable default
    service: LearningSessionService = Depends(get_session_service),
    current_user: User = Depends(get_current_user),
):
    """Alias for mark_understood supporting the /learning/chunks/{id}/understood format."""
    return mark_understood(chunk_id, payload, service, current_user)


@router.get('/session/document/{document_id}/nav-state')
def get_nav_state(
    document_id: int,
    service: LearningSessionService = Depends(get_session_service),
    current_user: User = Depends(get_current_user),
):
    """Return navigation constraints for the current user on this document.

    Used by the frontend to enforce no-skip during first pass and enable
    free-jump in review mode.
    """
    _check_document_access(service.db, current_user, document_id)
    learning_svc = LearningService(service.db)
    return {
        'document_id': document_id,
        'review_mode_unlocked': learning_svc.is_first_pass_complete(current_user.id, document_id),
        'max_unlocked_chunk_index': learning_svc.get_max_unlocked_chunk_index(current_user.id, document_id),
    }


def _check_qa_rate_limit(current_user: User):
    """P0 throttle for the most expensive endpoint in the app.

    Every QA call costs one Gemini embedding + a full-corpus BM25 scan + a
    Gemini generation, so an unthrottled endpoint is an unbounded API bill.
    Mirrors the mindmap-regen limiter: Redis fixed-window counter per user,
    fail-open (with an in-process fallback budget) on Redis outage.
    """
    _QA_RL_MAX = 20
    _QA_RL_WINDOW = 300  # 20 questions / 5 min / user
    redis_error = None
    try:
        from app.core.redis import redis_client
        _rl_key = f'ratelimit:qa:{current_user.id}'
        _rl_count = int(redis_client.incr(_rl_key) or 0)
        if _rl_count == 1:
            redis_client.expire(_rl_key, _QA_RL_WINDOW)
        if _rl_count > _QA_RL_MAX:
            _rl_ttl = max(int(redis_client.ttl(_rl_key) or _QA_RL_WINDOW), 1)
            raise HTTPException(
                status_code=429,
                detail=f'Question limit reached. Try again in {max(_rl_ttl // 60, 1)} minutes.',
            )
        return
    except HTTPException:
        raise
    except Exception as _rl_err:
        redis_error = _rl_err
        logger.warning(f'QA rate limiter unavailable (using in-process fallback): {_rl_err}')
    # Fail-OPEN to an in-process fallback budget (P-2: degradation must be
    # "stricter", never "free") — per-user window tracked in this worker only.
    import time as _time
    _now = _time.monotonic()
    _win = _qa_local_buckets.setdefault(current_user.id, [0, _now])
    if _now - _win[1] >= _QA_RL_WINDOW:
        _win[0], _win[1] = 0, _now
    _win[0] += 1
    if _win[0] > _QA_RL_MAX:
        raise HTTPException(
            status_code=429,
            detail=f'Question limit reached. Try again in {max(int(_QA_RL_WINDOW - (_now - _win[1])) // 60, 1)} minutes.',
        )


# user_id -> [count, window_start_monotonic] (in-process fail-open fallback only)
_qa_local_buckets: dict[int, list] = {}


@router.post('/session/qa', response_model=QAResponse)
def ask_question(
    payload: QARequest,
    rag: RagService = Depends(get_rag_service),
    current_user: User = Depends(get_current_user),
):
    """RAG-powered Q&A: retrieve relevant chunks and generate a Gemini-grounded answer.

    R-5 fix: mirrors QaService — resolves to the latest document version, keys
    the semantic cache by resolved doc_id + version + topic, and restores
    source provenance on cache hits so both QA paths behave identically.
    """
    _check_qa_rate_limit(current_user)
    _check_document_access(rag.db, current_user, payload.document_id)

    from app.clients.langfuse_client import langfuse_observation
    from app.evals.judges import judge_qa_groundedness

    # One Langfuse trace per QA request: retrieval (retriever obs) and the LLM
    # call (generation obs) nest inside automatically; the judge scores the trace.
    with langfuse_observation(
        name='rag-qa',
        as_type='chain',
        user_id=current_user.id,
        session_id=f'doc-{payload.document_id}',
        tags=['qa', 'rag'],
        metadata={
            'document_id': payload.document_id,
            'question': payload.question[:500],
            'topic_id': payload.topic_id,
            'rerank': payload.rerank,
            'feature': 'qa',
        },
    ) as trace:
        result = _ask_question_impl(payload, rag, current_user)
        # Eval: LLM-as-judge groundedness on the final answer (sampled).
        # Skip on outage / out-of-scope replies (no sources) so the metric is
        # not polluted by non-answer traffic.
        source_chunks = result.get('source_chunks') or []
        if source_chunks:
            context_snippet = '\n'.join((c.content or '')[:600] for c in source_chunks)[:4000]
            judge_qa_groundedness(payload.question, result.get('answer', ''), context_snippet, trace_obs=trace)
        return result


def _ask_question_impl(payload: QARequest, rag: RagService, current_user: User):
    """The core QA flow — no observability wrapper (keeps one trace per request)."""
    from app.models.document import Document
    from app.services.semantic_cache_service import SemanticCacheService
    from app.services.rag_service import OUT_OF_SCOPE_REPLY

    cache = SemanticCacheService()

    # 1. Resolve to the latest version (same resolution as QaService)
    doc = rag.db.query(Document).filter(Document.id == payload.document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail='Document not found')
    target_doc_id = payload.document_id
    latest_doc = rag.db.query(Document).filter(
        Document.code == doc.code,
        Document.is_latest == True
    ).first()
    if latest_doc:
        target_doc_id = latest_doc.id
        doc = latest_doc
    doc_version = doc.version
    topic_id = doc.topic_id

    # 2. Embed query (E-1: graceful reply on embedding outage instead of 500)
    try:
        query_vec = rag.embedding_client.embed_text(payload.question)
    except Exception:
        from app.services.rag_service import EMBEDDING_UNAVAILABLE_REPLY
        return {
            'question': payload.question,
            'answer': EMBEDDING_UNAVAILABLE_REPLY,
            'source_chunks': [],
        }

    # R-5: explicit topic override (matches QaService behavior)
    if payload.topic_id is not None:
        topic_id = payload.topic_id

    # 3. Check Semantic Cache (version + topic scoped)
    cached_entry = cache.lookup(
        document_id=target_doc_id,
        query_embedding=query_vec,
        topic_id=topic_id,
        doc_version=doc_version,
    )

    if cached_entry is not None:
        # Cache HIT! Restore provenance (R-6)
        from app.tasks.report_tasks import log_token_usage
        log_token_usage.delay(current_user.id, 'session_qa_cache_hit', 0, 0, 0.0, True, 0)
        source_chunk_ids = cached_entry.get('source_chunk_ids', [])
        context_chunks = []
        if source_chunk_ids:
            from app.models.chunk import Chunk
            context_chunks = rag.db.query(Chunk).filter(Chunk.id.in_(source_chunk_ids)).all()
        return {
            'question': payload.question,
            'answer': cached_entry['answer'],
            'source_chunks': context_chunks,
        }

    # 4. Cache MISS!
    try:
        context_chunks = rag.retrieve_chunks(target_doc_id, payload.question, top_k=3, rerank=payload.rerank)
    except Exception:
        # E-1: embedding outage surfaced mid-retrieval — reply clearly.
        from app.services.rag_service import EMBEDDING_UNAVAILABLE_REPLY
        return {
            'question': payload.question,
            'answer': EMBEDDING_UNAVAILABLE_REPLY,
            'source_chunks': [],
        }
    if not context_chunks:
        return {
            'question': payload.question,
            'answer': OUT_OF_SCOPE_REPLY,
            'source_chunks': [],
        }

    answer = rag.generate_answer(payload.question, context_chunks)

    # Store new result in Semantic Cache (version + topic scoped)
    cache.store(
        document_id=target_doc_id,
        question=payload.question,
        query_embedding=query_vec,
        answer=answer,
        topic_id=topic_id,
        doc_version=doc_version,
        source_chunk_ids=[c.id for c in context_chunks],
        page_refs=[c.page_no for c in context_chunks],
    )

    return {
        'question': payload.question,
        'answer': answer,
        'source_chunks': context_chunks,
    }

# ═══════════════════════════════════════════════════════════════════════════════
# PARENT-CHILD ADAPTIVE LEARNING ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Document Structure ────────────────────────────────────────────────────────

def _structure_payload(db: Session, user: User, document_id: int) -> dict:
    """Build the hierarchical structure payload for the cache/response."""
    service = LearningSessionService(db, user_id=user.id)
    raw = service.get_document_structure(document_id)

    # Fetch all child chunk attempts for the user on this document in one query
    from app.models.child_chunk_attempt import ChildChunkAttempt
    attempts = db.query(ChildChunkAttempt).filter(
        ChildChunkAttempt.user_id == user.id,
        ChildChunkAttempt.document_id == document_id
    ).all()
    attempts_by_child = {}
    for a in attempts:
        attempts_by_child.setdefault(a.child_chunk_id, []).append(a)

    # Serialise SQLAlchemy objects to plain dicts for JSON response.
    # M-4 fix: report REAL per-child attempt counts from the attempts already
    # fetched, while knowledge_score stays parent-derived (consistent with the
    # adaptive mastery model, M-3).
    parents_out = []
    for entry in raw['parents']:
        parent = entry['parent']
        progress = entry['progress']
        
        parent_completed = progress.is_completed if progress else False
        parent_score = progress.knowledge_score if progress else 0.0
        
        children_out = []
        for child in entry['children']:
            c_attempts = attempts_by_child.get(child.id, [])
            children_out.append({
                'id': child.id,
                'child_index': child.child_index,
                'chunk_index': child.chunk_index,
                'page_no': child.page_no,
                'content': child.content,
                'learning_card': child.learning_card,
                'token_count': child.token_count,
                'knowledge_score': 100.0 if parent_completed else 0.0,
                'attempt_count': len(c_attempts),
                'is_passed': parent_completed,
            })
        parents_out.append({
            'id': parent.id,
            'section_index': parent.section_index,
            'title': parent.title,
            'summary': parent.summary,
            'page_start': parent.page_start,
            'page_end': parent.page_end,
            'token_count': parent.token_count,
            'children': children_out,
            'progress': {
                'children_total': progress.children_total if progress else len(entry['children']),
                'children_completed': progress.children_completed if progress else 0,
                'knowledge_score': progress.knowledge_score if progress else 0.0,
                'is_completed': progress.is_completed if progress else False,
            } if progress else None,
        })

    return {
        'document_id': document_id,
        'has_parent_child': raw['has_parent_child'],
        'total_children': raw['total_children'],
        'parents': parents_out,
    }


@router.get('/session/document/{document_id}/structure')
def get_document_structure(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the full hierarchical learning structure of a document:
    parents (sections) → children (sub-chunks), with per-user progress.

    Cached per user (short TTL) — the payload is heavy (all parents/children +
    attempts) and is invalidated automatically on every answer submission via
    the ``resp:learning:{user_id}:`` prefix.
    """
    _check_document_access(db, current_user, document_id)
    from app.services.response_cache import get_cache
    key = f'resp:learning:{current_user.id}:structure:{document_id}'
    return get_cache().get_or_set(key, 60, lambda: _structure_payload(db, current_user, document_id))


# ── Mind Map ──────────────────────────────────────────────────────────────────

@router.get('/session/document/{document_id}/mindmap')
def get_mind_map(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns the interactive mind map tree for a document.

    P2 #5: cached 60s like /structure — the payload is heavy (prev-version
    parents + children are re-fetched and diffed on every page load) and is
    automatically refreshed for this user by the existing
    ``resp:learning:{user_id}:`` invalidation on every answer submission.
    """
    _check_document_access(db, current_user, document_id)
    from app.services.response_cache import get_cache
    key = f'resp:learning:{current_user.id}:mindmap:{document_id}'
    return get_cache().get_or_set(key, 60, lambda: _mind_map_payload(db, current_user, document_id))


def _mind_map_payload(db: Session, user: User, document_id: int) -> dict:
    from app.services.mind_map_service import MindMapService
    svc = MindMapService(db, user.id)
    return svc.build_mind_map(document_id)


@router.post('/session/document/{document_id}/mindmap/regenerate')
def regenerate_mind_map(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Regenerate the mind map concept tree for an existing document using the current
    parent chunk content. Useful when the mind map needs to be refreshed without
    a full re-ingestion.

    P1 #2: the LLM path is dispatched to Celery (it holds a worker for 10–30s);
    the deterministic path (numbered documents — no model call) still completes
    synchronously and returns the fresh map directly. LLM-path callers get
    202 + a pollable status URL. Dispatch failures degrade to synchronous
    regeneration so the feature never hard-breaks without a worker.
    """
    from app.models.document import Document
    from app.services.mind_map_service import build_regenerated_tree, tree_regenerate_mode
    from app.repositories.document_repository import DocumentRepository

    # M-2 fix: only admin/HOD/trainer may mutate the shared concept tree.
    if current_user.role not in (UserRole.admin, UserRole.hod, UserRole.trainer):
        raise HTTPException(status_code=403, detail='Only admins, HODs and trainers can regenerate the mind map.')

    # M-2 (rate limit): regeneration is an LLM-burning mutation of the SHARED
    # tree — cap it per user+document (fail-open matches the login limiter's
    # availability posture; the audit trail lives in Langfuse regardless).
    _RL_MAX = 5
    _RL_WINDOW = 600  # 5 regenerations / 10 min / user / document
    try:
        from app.core.redis import redis_client
        _rl_key = f'ratelimit:mindmap-regen:{current_user.id}:{document_id}'
        _rl_count = int(redis_client.incr(_rl_key) or 0)
        if _rl_count == 1:
            redis_client.expire(_rl_key, _RL_WINDOW)
        if _rl_count > _RL_MAX:
            _rl_ttl = max(int(redis_client.ttl(_rl_key) or _RL_WINDOW), 1)
            raise HTTPException(
                status_code=429,
                detail=f'Mind map regeneration limit reached. Try again in {max(_rl_ttl // 60, 1)} minutes.',
            )
    except HTTPException:
        raise
    except Exception as _rl_err:
        logger.warning(f'Mind map regenerate rate limiter unavailable (fail-open): {_rl_err}')

    _check_document_access(db, current_user, document_id)

    document = db.query(Document).filter(Document.id == document_id).first()
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    mode = tree_regenerate_mode(db, document)

    # ── LLM path: dispatch to Celery (P1 #2) ─────────────────────────────────
    if mode == 'llm':
        try:
            from app.tasks import mindmap_tasks
            async_result = mindmap_tasks.regenerate_mind_map_tree.delay(document_id, current_user.id)
            task_id = getattr(async_result, 'id', None)
        except Exception as dispatch_err:
            logger.warning(f'Celery dispatch failed for mindmap regen (sync fallback): {dispatch_err}')
            task_id = None

        if task_id:
            from app.tasks.mindmap_tasks import set_status
            set_status(document_id, 'queued', {'task_id': task_id})
            return JSONResponse(
                status_code=202,
                content={
                    'document_id': document_id,
                    'regeneration': 'async',
                    'task_id': task_id,
                    'status_url': f'/learning/session/document/{document_id}/mindmap/regenerate/status',
                },
            )
        # No worker available — degrade to synchronous so the feature still works.
        mind_map_tree, _used_llm = build_regenerated_tree(db, document, user_id=current_user.id)
        DocumentRepository(db).update(document, mind_map_json=mind_map_tree)
    else:
        # Deterministic path: cheap (titles + stored tree only), no model call.
        mind_map_tree, _used_llm = build_regenerated_tree(db, document, user_id=current_user.id)
        DocumentRepository(db).update(document, mind_map_json=mind_map_tree)

    from app.services.mind_map_service import MindMapService
    svc = MindMapService(db, current_user.id)
    return {
        **svc.build_mind_map(document_id),
        'regenerated': True,
        'concept_nodes_count': len(mind_map_tree),
    }


@router.get('/session/document/{document_id}/mindmap/regenerate/status')
def mind_map_regen_status(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll the status of an async (Celery) mind map regeneration (P1 #2)."""
    _check_document_access(db, current_user, document_id)
    from app.core.redis import redis_client
    from app.tasks.mindmap_tasks import status_key
    raw = redis_client.get(status_key(document_id))
    if not raw:
        return {'document_id': document_id, 'state': 'unknown'}
    try:
        return json.loads(raw)
    except Exception:
        return {'document_id': document_id, 'state': 'unknown'}


# ── Child Chunk Q&A ───────────────────────────────────────────────────────────

def _child_question_payload(db: Session, user: User, chunk_id: int) -> dict:
    """Build the adaptive question payload for the cache/response."""
    from app.models.chunk import Chunk as ChunkModel
    from app.agents.adaptive_agent_service import AdaptiveAgentService
    from app.services.adaptive_mcq_service import AdaptiveMcqService

    chunk = db.query(ChunkModel).filter(ChunkModel.id == chunk_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail='Chunk not found')
    _check_document_access(db, user, chunk.document_id)

    coordinator = AdaptiveAgentService(db, user_id=user.id)
    question_data = coordinator.get_next_question(user.id, chunk)

    # Legacy contract preserved: knowledge_score / is_passed stay parent-derived
    # (section mastery), while the new `mastery` block carries the concept-level
    # capability state so the two signals never conflict.
    adaptive = AdaptiveMcqService(db)
    knowledge_score = adaptive.get_child_knowledge_score(user.id, chunk_id)

    return {
        **question_data,
        'attempt_count': question_data['mastery']['attempts'],
        'knowledge_score': knowledge_score,
        'is_passed': knowledge_score >= 80.0,
    }


@router.get('/session/child/{chunk_id}/question')
def get_child_question(
    chunk_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Agent-driven next question for a child chunk's parent section.
    Difficulty and format adapt to the learner's concept mastery
    (CurriculumAgent → QuestionGeneratorAgent), and the response carries the
    learner's mastery state + the agents' decision trace.

    Cached per user (short TTL) — cleared automatically when the answer is
    submitted (``resp:learning:{user_id}:`` prefix invalidation), so each new
    attempt still gets a fresh adaptive question.
    """
    from app.models.chunk import Chunk as ChunkModel
    chunk = db.query(ChunkModel).filter(ChunkModel.id == chunk_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail='Chunk not found')
    _check_document_access(db, current_user, chunk.document_id)

    from app.services.response_cache import get_cache
    key = f'resp:learning:{current_user.id}:question:{chunk_id}'
    return get_cache().get_or_set(key, 30, lambda: _child_question_payload(db, current_user, chunk_id))


class ChildAnswerPayload(BaseModel):
    mcq_id: int | None = None
    question_data: dict | None = None
    selected_option: str
    time_spent_seconds: int = 0


@router.post('/session/child/{chunk_id}/answer')
def submit_child_answer(
    chunk_id: int,
    payload: ChildAnswerPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Submit an answer for a child chunk.
    Returns: is_correct, explanation, re_explanation, child_passed,
             knowledge_score, next_child, parent_completed, document_completed.
    """
    from app.models.chunk import Chunk as ChunkModel
    from app.models.mcq import MCQBank

    chunk = db.query(ChunkModel).filter(ChunkModel.id == chunk_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail='Chunk not found')
    _check_document_access(db, current_user, chunk.document_id)

    q_data = payload.question_data
    if not q_data and payload.mcq_id:
        mcq = db.query(MCQBank).filter(MCQBank.id == payload.mcq_id).first()
        if mcq:
            q_data = {
                'id': mcq.id,
                'mcq_id': mcq.id,
                'question': mcq.question,
                'options': mcq.options,
                'correct_option': mcq.correct_option,
                'explanation': mcq.explanation,
                'difficulty': mcq.difficulty,
            }
    elif q_data and payload.mcq_id and not q_data.get('mcq_id'):
        q_data['mcq_id'] = payload.mcq_id

    if not q_data:
        raise HTTPException(status_code=400, detail='Either mcq_id or question_data must be provided')

    from app.agents.adaptive_agent_service import AdaptiveAgentService
    coordinator = AdaptiveAgentService(db, user_id=current_user.id)
    return coordinator.submit_answer(
        user_id=current_user.id,
        chunk=chunk,
        question_data=q_data,
        selected_option=payload.selected_option,
        time_taken_seconds=payload.time_spent_seconds,
    )


# ── Agent capability profile ─────────────────────────────────────────────────

@router.get('/agent/profile')
def get_agent_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Learner capability profile: concept mastery, weakness heatmap, strengths
    and a prioritized study plan (RecommenderAgent).
    """
    from app.agents.adaptive_agent_service import AdaptiveAgentService
    coordinator = AdaptiveAgentService(db, user_id=current_user.id)
    return coordinator.recommender.build_profile(current_user.id)


@router.get('/agent/profile/{document_id}')
def get_agent_profile_for_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Per-document capability profile for the learner."""
    _check_document_access(db, current_user, document_id)
    from app.agents.adaptive_agent_service import AdaptiveAgentService
    coordinator = AdaptiveAgentService(db, user_id=current_user.id)
    return coordinator.recommender.build_profile(current_user.id, document_id=document_id)


@router.get('/agent/mastery-history')
def get_mastery_history(
    document_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Score-over-time trajectory per concept (EMA replayed from attempts).
    Optional ?document_id scopes the history to one document.
    """
    if document_id:
        _check_document_access(db, current_user, document_id)
    from app.agents.adaptive_agent_service import AdaptiveAgentService
    coordinator = AdaptiveAgentService(db, user_id=current_user.id)
    return coordinator.recommender.build_mastery_history(current_user.id, document_id)
