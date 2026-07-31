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

    result = []
    for a in assignments:
        doc = db.query(Document).filter(Document.id == a.document_id).first()
        if not doc:
            continue
        progress = (
            db.query(UserProgress)
            .filter(
                UserProgress.user_id == current_user.id,
                UserProgress.document_id == a.document_id,
            )
            .first()
        )
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
    return result


@router.get('/progress/dashboard')
def get_progress_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Consolidated dashboard: assignments, progress, weak areas, completion stats."""
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
    for p in all_progress:
        if 0 < p.completion_percentage < 100:
            doc = db.query(Document).filter(Document.id == p.document_id).first()
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

    weak_area_items = []
    for w in weak_areas:
        doc = db.query(Document).filter(Document.id == w.document_id).first()
        topic = db.query(Topic).filter(Topic.id == w.topic_id).first()
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

    return {
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
    from app.models.training import TrainingAssignment
    from app.models.document import Document
    from app.models.user_mcq_attempt import UserMcqAttempt

    assignments = db.query(TrainingAssignment).filter(
        TrainingAssignment.user_id == current_user.id
    ).order_by(TrainingAssignment.created_at.desc()).all()

    record = []
    for a in assignments:
        doc = db.query(Document).filter(Document.id == a.document_id).first() if a.document_id else None
        # Best MCQ score for this document
        best_attempt = None
        if a.document_id:
            best_attempt = (
                db.query(UserMcqAttempt)
                .filter(
                    UserMcqAttempt.user_id == current_user.id,
                    UserMcqAttempt.document_id == a.document_id,
                )
                .order_by(UserMcqAttempt.score.desc())
                .first()
            )
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
    return {
        'user_id': current_user.id,
        'full_name': current_user.full_name,
        'employee_code': current_user.employee_code,
        'department': current_user.department,
        'training_record': record,
    }


@router.get('/paths')
def get_user_training_paths(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Return training paths (curricula) assigned to the logged-in user,
    grouped by assigned topics/subjects with completion stats.
    """
    from app.models.training import TrainingAssignment
    from app.models.document import Document
    from app.models.topic import Topic
    from app.models.user_progress import UserProgress

    assignments = db.query(TrainingAssignment).filter(
        TrainingAssignment.user_id == current_user.id
    ).all()

    if not assignments:
        return []

    # Group assigned documents by Topic
    topic_groups = {}
    for a in assignments:
        if not a.document_id:
            continue
        doc = db.query(Document).filter(Document.id == a.document_id).first()
        if not doc:
            continue

        topic_id = doc.topic_id or 0
        if topic_id not in topic_groups:
            topic = db.query(Topic).filter(Topic.id == topic_id).first() if topic_id else None
            topic_groups[topic_id] = {
                'id': topic_id or 1,
                'name': topic.title if topic else 'Core SOP Learning Path',
                'description': getattr(topic, 'description', None) or 'Role-based Standard Operating Procedures curriculum',
                'documents': [],
            }

        progress = db.query(UserProgress).filter(
            UserProgress.user_id == current_user.id,
            UserProgress.document_id == doc.id,
        ).first()

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

    return paths


@router.get('/paths/{path_id}/modules')
def get_user_path_modules(
    path_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return modules (documents) for a specific user-assigned training path."""
    from app.models.training import TrainingAssignment
    from app.models.document import Document
    from app.models.user_progress import UserProgress

    assignments = db.query(TrainingAssignment).filter(
        TrainingAssignment.user_id == current_user.id
    ).all()

    modules = []
    for a in assignments:
        if not a.document_id:
            continue
        doc = db.query(Document).filter(Document.id == a.document_id).first()
        if not doc:
            continue

        if (doc.topic_id or 1) == path_id or path_id == 1:
            progress = db.query(UserProgress).filter(
                UserProgress.user_id == current_user.id,
                UserProgress.document_id == doc.id,
            ).first()

            is_completed = (a.status == 'completed') or (progress and progress.completion_percentage >= 100.0)
            modules.append({
                'id': doc.id,
                'document_id': doc.id,
                'title': f"{doc.code}: {doc.title}",
                'code': doc.code,
                'completed': is_completed,
                'documents_count': 1,
            })

    return modules

