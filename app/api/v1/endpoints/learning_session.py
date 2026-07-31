"""
Adaptive Learning Session Endpoints — Phase 3 Complete

GET  /learning/session/document/{document_id}/start   → Load chunks + resume or start from chunk 1
GET  /learning/session/document/{document_id}/resume  → Return the last saved chunk position
GET  /learning/session/chunk/{chunk_id}               → Get a specific chunk + MCQ
POST /learning/session/chunk/{chunk_id}/answer        → Submit answer; persist progress
POST /learning/session/qa                             → RAG Q&A scoped to document
POST /learning/session/chunk/{chunk_id}/understood    → Mark chunk as understood (no MCQ path)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User, UserRole
from app.services.learning_service import LearningService
from app.services.learning_session_service import LearningSessionService
from app.services.rag_service import RagService

router = APIRouter(prefix='/learning', tags=['learning-session'])


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
    payload: dict = {},
    service: LearningSessionService = Depends(get_session_service),
    current_user: User = Depends(get_current_user),
):
    """Mark a chunk as understood without an MCQ (review mode shortcut)."""
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
    payload: dict = {},
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


@router.post('/session/qa', response_model=QAResponse)
def ask_question(
    payload: QARequest,
    rag: RagService = Depends(get_rag_service),
    current_user: User = Depends(get_current_user),
):
    """RAG-powered Q&A: retrieve relevant chunks and generate a Gemini-grounded answer."""
    _check_document_access(rag.db, current_user, payload.document_id)

    from app.services.semantic_cache_service import SemanticCacheService
    cache = SemanticCacheService()

    # 1. Embed query
    query_vec = rag.embedding_client.embed_text(payload.question)

    # 2. Check Semantic Cache
    cached_answer = cache.lookup(document_id=payload.document_id, query_embedding=query_vec)

    if cached_answer is not None:
        # Cache HIT!
        from app.tasks.report_tasks import log_token_usage
        log_token_usage.delay(current_user.id, 'session_qa_cache_hit', 0, 0, 0.0, True, 0)
        return {
            'question': payload.question,
            'answer': cached_answer,
            'source_chunks': [],
        }

    # 3. Cache MISS!
    context_chunks = rag.retrieve_chunks(payload.document_id, payload.question, top_k=3)
    if not context_chunks:
        from app.services.rag_service import OUT_OF_SCOPE_REPLY
        return {
            'question': payload.question,
            'answer': OUT_OF_SCOPE_REPLY,
            'source_chunks': [],
        }

    answer = rag.generate_answer(payload.question, context_chunks)

    # Store new result in Semantic Cache
    cache.store(
        document_id=payload.document_id,
        question=payload.question,
        query_embedding=query_vec,
        answer=answer
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

@router.get('/session/document/{document_id}/structure')
def get_document_structure(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the full hierarchical learning structure of a document:
    parents (sections) → children (sub-chunks), with per-user progress.
    """
    _check_document_access(db, current_user, document_id)
    service = LearningSessionService(db, user_id=current_user.id)
    raw = service.get_document_structure(document_id)

    # Fetch all child chunk attempts for the user on this document in one query
    from app.models.child_chunk_attempt import ChildChunkAttempt
    attempts = db.query(ChildChunkAttempt).filter(
        ChildChunkAttempt.user_id == current_user.id,
        ChildChunkAttempt.document_id == document_id
    ).all()
    attempts_by_child = {}
    for a in attempts:
        attempts_by_child.setdefault(a.child_chunk_id, []).append(a)

    # Serialise SQLAlchemy objects to plain dicts for JSON response
    parents_out = []
    for entry in raw['parents']:
        parent = entry['parent']
        progress = entry['progress']
        children_out = []
        for child in entry['children']:
            c_attempts = attempts_by_child.get(child.id, [])
            attempt_count = len(c_attempts)
            correct_count = sum(1 for a in c_attempts if a.is_correct)
            score = round((correct_count / attempt_count) * 100, 2) if attempt_count > 0 else 0.0
            
            children_out.append({
                'id': child.id,
                'child_index': child.child_index,
                'chunk_index': child.chunk_index,
                'page_no': child.page_no,
                'content': child.content,
                'learning_card': child.learning_card,
                'token_count': child.token_count,
                'knowledge_score': score,
                'attempt_count': attempt_count,
                'is_passed': score >= 80.0,
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


# ── Mind Map ──────────────────────────────────────────────────────────────────

@router.get('/session/document/{document_id}/mindmap')
def get_mind_map(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns the interactive mind map tree for a document."""
    _check_document_access(db, current_user, document_id)
    from app.services.mind_map_service import MindMapService
    svc = MindMapService(db, current_user.id)
    return svc.build_mind_map(document_id)


# ── Child Chunk Q&A ───────────────────────────────────────────────────────────

@router.get('/session/child/{chunk_id}/question')
def get_child_question(
    chunk_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the next adaptive question for a child chunk.
    Difficulty escalates with each attempt: easy → medium → hard.
    """
    from app.models.chunk import Chunk as ChunkModel
    from app.services.adaptive_mcq_service import AdaptiveMcqService

    chunk = db.query(ChunkModel).filter(ChunkModel.id == chunk_id).first()
    if not chunk:
        raise HTTPException(status_code=404, detail='Chunk not found')
    _check_document_access(db, current_user, chunk.document_id)

    adaptive = AdaptiveMcqService(db)
    question_data = adaptive.get_next_question(current_user.id, chunk)
    attempt_count = adaptive.get_child_attempt_count(current_user.id, chunk_id)
    knowledge_score = adaptive.get_child_knowledge_score(current_user.id, chunk_id)

    return {
        **question_data,
        'chunk_id': chunk_id,
        'attempt_count': attempt_count,
        'knowledge_score': knowledge_score,
        'is_passed': knowledge_score >= 80.0,
    }


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

    service = LearningSessionService(db, user_id=current_user.id)
    return service.submit_child_answer(
        user_id=current_user.id,
        chunk_id=chunk_id,
        question_data=q_data,
        selected_option=payload.selected_option,
        time_taken_seconds=payload.time_spent_seconds,
    )
