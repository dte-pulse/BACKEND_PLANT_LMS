from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.progress import ProgressRead, ProgressUpdate
from app.services.progress_service import ProgressService
from app.schemas.weakness import WeaknessRead, WeaknessUpdate
from app.services.weakness_service import WeaknessService

router = APIRouter(prefix='/learning', tags=['learning'])


def get_progress_service(db: Session = Depends(get_db)):
    return ProgressService(db)


def get_weakness_service(db: Session = Depends(get_db)):
    return WeaknessService(db)


# ─── Progress endpoints ───────────────────────────────────────────────────────

@router.get('/assigned')
def get_assigned_documents(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return documents assigned to this user via TrainingAssignments,
    enriched with their current progress percentage.
    """
    from app.services.response_cache import get_cache, LEARNING_CACHE_TTL
    cache = get_cache()
    key = f"resp:learning:{current_user.id}:assigned"
    cached = cache.get(key)
    if cached is not None:
        return cached

    from app.models.training import TrainingAssignment
    from app.models.document import Document
    from app.models.user_progress import UserProgress

    assignments = (
        db.query(TrainingAssignment)
        .filter(
            TrainingAssignment.user_id == current_user.id,
            TrainingAssignment.document_id.isnot(None),
        )
        .all()
    )

    # N+1 fix: batch-fetch documents + progress rows in two queries.
    doc_ids = {a.document_id for a in assignments if a.document_id}
    docs = {d.id: d for d in db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
    progress_by_doc = {
        p.document_id: p
        for p in db.query(UserProgress).filter(
            UserProgress.user_id == current_user.id,
            UserProgress.document_id.in_(doc_ids),
        ).all()
    } if doc_ids else {}

    result = []
    for a in assignments:
        doc = docs.get(a.document_id)
        if not doc:
            continue
        progress = progress_by_doc.get(a.document_id)
        result.append({
            'assignment_id': a.id,
            'document_id': doc.id,
            'document_code': doc.code,
            'document_title': doc.title,
            'document_status': doc.status,
            'training_type': a.training_type,
            'assignment_status': a.status,
            'due_date': a.due_date,
            'completion_percentage': progress.completion_percentage if progress else 0.0,
            'current_chunk_id': progress.current_chunk_id if progress else None,
            'time_spent_seconds': progress.time_spent_seconds if progress else 0,
            'last_accessed_at': progress.last_accessed_at if progress else None,
        })
    cache.set(key, result, LEARNING_CACHE_TTL)
    return result


@router.get('/progress/dashboard')
def get_progress_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Consolidated dashboard: assignments, progress, weak areas, completion stats."""
    from app.services.response_cache import get_cache, LEARNING_CACHE_TTL
    cache = get_cache()
    key = f"resp:learning:{current_user.id}:dashboard"
    cached = cache.get(key)
    if cached is not None:
        return cached

    from app.models.training import TrainingAssignment
    from app.models.user_progress import UserProgress
    from app.models.user_weakness_profile import UserWeaknessProfile
    from app.models.document import Document
    from app.models.topic import Topic

    assignments = db.query(TrainingAssignment).filter(
        TrainingAssignment.user_id == current_user.id
    ).all()

    total = len(assignments)
    completed = sum(1 for a in assignments if a.status == 'completed')
    pending = sum(1 for a in assignments if a.status in ('assigned', 'pending'))

    all_progress = db.query(UserProgress).filter(
        UserProgress.user_id == current_user.id
    ).all()
    avg_completion = (
        sum(p.completion_percentage for p in all_progress) / len(all_progress)
        if all_progress else 0.0
    )

    weak_areas = db.query(UserWeaknessProfile).filter(
        UserWeaknessProfile.user_id == current_user.id,
        UserWeaknessProfile.score < 80,
    ).all()
    critical_areas = [w for w in weak_areas if w.is_critical]

    in_progress_docs = []
    in_progress_ids = [p.document_id for p in all_progress if 0 < p.completion_percentage < 100]
    docs_map = {
        d.id: d for d in db.query(Document).filter(Document.id.in_(in_progress_ids)).all()
    } if in_progress_ids else {}
    for p in all_progress:
        if 0 < p.completion_percentage < 100:
            doc = docs_map.get(p.document_id)
            if doc:
                in_progress_docs.append({
                    'document_id': doc.id,
                    'document_code': doc.code,
                    'document_title': doc.title,
                    'completion_percentage': p.completion_percentage,
                    'current_chunk_id': p.current_chunk_id,
                    'time_spent_seconds': p.time_spent_seconds,
                    'last_accessed_at': p.last_accessed_at,
                })

    # N+1 fix: batch-fetch documents + topics referenced by weak areas.
    weak_doc_ids = {w.document_id for w in weak_areas if w.document_id}
    weak_topic_ids = {w.topic_id for w in weak_areas if w.topic_id}
    weak_docs = {d.id: d for d in db.query(Document).filter(Document.id.in_(weak_doc_ids)).all()} if weak_doc_ids else {}
    weak_topics_map = {t.id: t for t in db.query(Topic).filter(Topic.id.in_(weak_topic_ids)).all()} if weak_topic_ids else {}

    weak_area_items = []
    for w in weak_areas:
        doc = weak_docs.get(w.document_id)
        topic = weak_topics_map.get(w.topic_id)
        related_assignments = [
            a for a in assignments
            if a.document_id == w.document_id and a.training_type in ('need_based', 'sop', 'cgmp', 'induction', 'contractual', 'ojt')
        ]
        latest_assignment = max(related_assignments, key=lambda a: a.id) if related_assignments else None
        weak_area_items.append({
            'topic_id': w.topic_id,
            'topic_title': topic.title if topic else f'Topic #{w.topic_id}',
            'document_id': w.document_id,
            'document_code': doc.code if doc else None,
            'document_title': doc.title if doc else None,
            'score': w.score,
            'attempt_count': w.attempt_count,
            'is_critical': w.is_critical,
            'retraining_status': latest_assignment.status if latest_assignment else None,
            'recommended_action': 'Immediate retraining required' if w.is_critical else 'Review and reattempt recommended',
        })

    payload = {
        'summary': {
            'total_assignments': total,
            'completed': completed,
            'pending': pending,
            'avg_completion_pct': round(avg_completion, 2),
            'weak_topics': len(weak_areas),
            'critical_topics': len(critical_areas),
        },
        'in_progress': in_progress_docs,
        'weak_areas': weak_area_items,
    }
    cache.set(key, payload, LEARNING_CACHE_TTL)
    return payload


@router.get('/progress', response_model=list[ProgressRead])
def get_all_progress(
    current_user: User = Depends(get_current_user),
    service: ProgressService = Depends(get_progress_service)
):
    return service.list_user_progress(current_user.id)


@router.get('/progress/{document_id}', response_model=ProgressRead)
def get_document_progress(
    document_id: int,
    current_user: User = Depends(get_current_user),
    service: ProgressService = Depends(get_progress_service)
):
    progress = service.get_user_progress(current_user.id, document_id)
    if not progress:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Progress not found')
    return progress


@router.post('/progress', response_model=ProgressRead)
def update_progress(
    payload: ProgressUpdate,
    current_user: User = Depends(get_current_user),
    service: ProgressService = Depends(get_progress_service)
):
    return service.update_progress(current_user.id, payload)


@router.get('/training-record')
def get_training_record(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the individual training record (Annexure-V style) for a trainee."""
    from app.services.response_cache import get_cache, LEARNING_CACHE_TTL
    cache = get_cache()
    key = f"resp:learning:{current_user.id}:training-record"
    cached = cache.get(key)
    if cached is not None:
        return cached

    from app.models.training import TrainingAssignment
    from app.models.document import Document
    from app.models.user_mcq_attempt import UserMcqAttempt

    assignments = db.query(TrainingAssignment).filter(
        TrainingAssignment.user_id == current_user.id
    ).order_by(TrainingAssignment.created_at.desc()).all()

    # N+1 fix: batch-fetch documents + best attempt per document.
    doc_ids = {a.document_id for a in assignments if a.document_id}
    docs = {d.id: d for d in db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
    best_by_doc: dict[int, UserMcqAttempt] = {}
    if doc_ids:
        for att in db.query(UserMcqAttempt).filter(
            UserMcqAttempt.user_id == current_user.id,
            UserMcqAttempt.document_id.in_(doc_ids),
        ).all():
            current = best_by_doc.get(att.document_id)
            if current is None or (att.score or 0) > (current.score or 0):
                best_by_doc[att.document_id] = att

    record = []
    for a in assignments:
        doc = docs.get(a.document_id)
        best_attempt = best_by_doc.get(a.document_id) if a.document_id else None
        record.append({
            'training_type': a.training_type,
            'document_code': doc.code if doc else None,
            'document_title': doc.title if doc else None,
            'assignment_status': a.status,
            'due_date': a.due_date,
            'best_score': best_attempt.score if best_attempt else None,
            'passed': best_attempt.passed if best_attempt else None,
            'created_at': a.created_at,
        })
    payload = {
        'user_id': current_user.id,
        'full_name': current_user.full_name,
        'employee_code': current_user.employee_code,
        'department': current_user.department,
        'training_record': record,
    }
    cache.set(key, payload, LEARNING_CACHE_TTL)
    return payload


@router.get('/paths')
def get_user_training_paths(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return training paths (curricula) assigned to the logged-in user,
    grouped by assigned topics/subjects with completion stats.
    """
    from app.services.response_cache import get_cache, LEARNING_CACHE_TTL
    cache = get_cache()
    key = f"resp:learning:{current_user.id}:paths"
    cached = cache.get(key)
    if cached is not None:
        return cached

    from app.models.training import TrainingAssignment
    from app.models.document import Document
    from app.models.topic import Topic
    from app.models.user_progress import UserProgress

    assignments = db.query(TrainingAssignment).filter(
        TrainingAssignment.user_id == current_user.id
    ).all()

    if not assignments:
        return []

    # N+1 fix: batch-fetch documents, topics + progress rows up front.
    doc_ids = {a.document_id for a in assignments if a.document_id}
    docs = {d.id: d for d in db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
    topic_ids = {d.topic_id for d in docs.values() if d.topic_id}
    topics_map = {t.id: t for t in db.query(Topic).filter(Topic.id.in_(topic_ids)).all()} if topic_ids else {}
    progress_by_doc = {
        p.document_id: p
        for p in db.query(UserProgress).filter(
            UserProgress.user_id == current_user.id,
            UserProgress.document_id.in_(doc_ids),
        ).all()
    } if doc_ids else {}

    # Group assigned documents by Topic
    topic_groups = {}
    for a in assignments:
        if not a.document_id:
            continue
        doc = docs.get(a.document_id)
        if not doc:
            continue

        topic_id = doc.topic_id or 0
        if topic_id not in topic_groups:
            topic = topics_map.get(topic_id)
            topic_groups[topic_id] = {
                'id': topic_id or 1,
                'name': topic.title if topic else 'Core SOP Learning Path',
                'description': getattr(topic, 'description', None) or 'Role-based Standard Operating Procedures curriculum',
                'documents': [],
            }

        progress = progress_by_doc.get(doc.id)

        is_completed = (a.status == 'completed') or (progress and progress.completion_percentage >= 100.0)

        topic_groups[topic_id]['documents'].append({
            'id': doc.id,
            'code': doc.code,
            'title': doc.title,
            'assignment_status': a.status,
            'completed': is_completed,
            'completion_percentage': progress.completion_percentage if progress else 0.0,
        })

    paths = []
    for t_id, t_data in topic_groups.items():
        docs = t_data['documents']
        total_mods = len(docs)
        completed_mods = sum(1 for d in docs if d['completed'])
        paths.append({
            'id': t_data['id'],
            'name': t_data['name'],
            'description': t_data['description'],
            'total_modules': total_mods,
            'completed_modules': completed_mods,
            'documents_count': total_mods,
            'estimated_duration': f'{total_mods * 30} mins',
        })

    cache.set(key, paths, LEARNING_CACHE_TTL)
    return paths


@router.get('/paths/{path_id}/modules')
def get_user_path_modules(
    path_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return modules (documents) for a specific user-assigned training path."""
    from app.services.response_cache import get_cache, LEARNING_CACHE_TTL
    cache = get_cache()
    key = f"resp:learning:{current_user.id}:path-modules:{path_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    from app.models.training import TrainingAssignment
    from app.models.document import Document
    from app.models.user_progress import UserProgress

    assignments = db.query(TrainingAssignment).filter(
        TrainingAssignment.user_id == current_user.id
    ).all()

    # N+1 fix: batch-fetch documents + progress rows up front.
    doc_ids = {a.document_id for a in assignments if a.document_id}
    docs = {d.id: d for d in db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
    progress_by_doc = {
        p.document_id: p
        for p in db.query(UserProgress).filter(
            UserProgress.user_id == current_user.id,
            UserProgress.document_id.in_(doc_ids),
        ).all()
    } if doc_ids else {}

    modules = []
    for a in assignments:
        if not a.document_id:
            continue
        doc = docs.get(a.document_id)
        if not doc:
            continue

        if (doc.topic_id or 1) == path_id or path_id == 1:
            progress = progress_by_doc.get(doc.id)

            is_completed = (a.status == 'completed') or (progress and progress.completion_percentage >= 100.0)
            modules.append({
                'id': doc.id,
                'document_id': doc.id,
                'title': f"{doc.code}: {doc.title}",
                'code': doc.code,
                'completed': is_completed,
                'documents_count': 1,
            })

    cache.set(key, modules, LEARNING_CACHE_TTL)
    return modules

