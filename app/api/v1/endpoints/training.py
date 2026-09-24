"""
Phase 5 — Training Operations Endpoint
All training workflow types: induction, ojt, sop, cgmp, external, need-based, contractual.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.models.user import User, UserRole
from app.repositories.training_repository import TrainingRepository
from app.schemas.training_evidence import TrainingEvidenceRead
from app.services.training_service import TrainingService
from app.services.notification_service import NotificationService
from app.storage.file_storage import UploadValidationError

router = APIRouter(prefix='/training', tags=['training'])


def get_training_service(db: Session = Depends(get_db)):
    return TrainingService(TrainingRepository(db))


# ─── Schemas ─────────────────────────────────────────────────────────────────

class AssignRequest(BaseModel):
    user_id: int
    document_id: Optional[int] = None
    training_type: str
    trainer_id: Optional[int] = None
    due_date: Optional[datetime] = None
    notes: Optional[str] = None


class BulkAssignRequest(BaseModel):
    user_ids: list[int]
    document_id: int
    training_type: str
    trainer_id: Optional[int] = None
    due_date: Optional[datetime] = None
    notes: Optional[str] = None


class OJTVerifyRequest(BaseModel):
    assignment_id: int


class ExternalTrainingRequest(BaseModel):
    user_id: int
    document_id: Optional[int] = None
    certificate_url: Optional[str] = None
    notes: Optional[str] = None
    provider: Optional[str] = None
    venue: Optional[str] = None
    duration_hours: Optional[int] = None


class NeedBasedRequest(BaseModel):
    document_id: int
    user_id: Optional[int] = None
    reason: str
    notes: Optional[str] = None


class CompleteRequest(BaseModel):
    assignment_id: int


class ReviewAssignmentRequest(BaseModel):
    approved: bool
    notes: Optional[str] = None


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get('/assignments')
def list_all_assignments(
    department: Optional[str] = None,
    user_id: Optional[int] = None,
    training_type: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: TrainingService = Depends(get_training_service),
):
    return service.list_assignments(
        department=department,
        user_id=user_id,
        training_type=training_type,
        status=status,
    )


@router.get('/assignments/my')
def my_assignments(
    current_user: User = Depends(get_current_user),
    service: TrainingService = Depends(get_training_service),
):
    return service.list_user_assignments(current_user.id)


@router.post('/assign', status_code=status.HTTP_201_CREATED)
def assign_training(
    payload: AssignRequest,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    db: Session = Depends(get_db),
    service: TrainingService = Depends(get_training_service),
):
    from app.schemas.training import TrainingAssignmentCreate
    a = service.create_assignment(TrainingAssignmentCreate(
        user_id=payload.user_id,
        document_id=payload.document_id,
        training_type=payload.training_type,
        status='assigned',
        trainer_id=payload.trainer_id,
        due_date=payload.due_date,
        assigned_by_id=current_user.id,
        notes=payload.notes,
    ))
    # Trigger notification
    notif_svc = NotificationService(db)
    notif_svc.notify_training_due(
        payload.user_id,
        payload.training_type,
        payload.due_date.strftime('%Y-%m-%d') if payload.due_date else 'TBD'
    )
    return a


@router.post('/assign/bulk', status_code=status.HTTP_201_CREATED)
def bulk_assign_training(
    payload: BulkAssignRequest,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    db: Session = Depends(get_db),
    service: TrainingService = Depends(get_training_service),
):
    from app.schemas.training import TrainingAssignmentCreate
    notif_svc = NotificationService(db)
    created = []
    for uid in payload.user_ids:
        a = service.create_assignment(TrainingAssignmentCreate(
            user_id=uid,
            document_id=payload.document_id,
            training_type=payload.training_type,
            status='assigned',
            trainer_id=payload.trainer_id,
            due_date=payload.due_date,
            assigned_by_id=current_user.id,
            notes=payload.notes,
        ))
        created.append(a)
        notif_svc.notify_training_due(
            uid,
            payload.training_type,
            payload.due_date.strftime('%Y-%m-%d') if payload.due_date else 'TBD',
        )
    return {'created': len(created), 'assignments': created}


@router.post('/induction/trigger')
def trigger_induction(
    payload: BulkAssignRequest,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: TrainingService = Depends(get_training_service),
):
    return service.trigger_induction_training(payload.user_ids[0], [payload.document_id], assigned_by_id=current_user.id)


@router.post('/ojt/verify')
def verify_ojt(
    payload: OJTVerifyRequest,
    current_user: User = Depends(require_role([UserRole.trainer])),
    service: TrainingService = Depends(get_training_service),
):
    try:
        return service.verify_ojt(payload.assignment_id, current_user.id)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post('/external/log', status_code=status.HTTP_201_CREATED)
def log_external_training(
    payload: ExternalTrainingRequest,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: TrainingService = Depends(get_training_service),
):
    return service.log_external_training(
        payload.user_id,
        payload.document_id or 0,
        payload.certificate_url,
        notes=payload.notes,
        provider=payload.provider,
        venue=payload.venue,
        duration_hours=payload.duration_hours,
        assigned_by_id=current_user.id,
    )


@router.post('/need-based/request', status_code=status.HTTP_201_CREATED)
def request_need_based_training(
    payload: NeedBasedRequest,
    current_user: User = Depends(get_current_user),
    service: TrainingService = Depends(get_training_service),
):
    return service.submit_need_based_request(
        payload.user_id or current_user.id,
        payload.document_id,
        payload.reason,
        notes=payload.notes,
        assigned_by_id=current_user.id,
    )


@router.post('/sop/trigger')
def trigger_sop_training(
    payload: BulkAssignRequest,
    current_user: User = Depends(require_role([UserRole.admin])),
    db: Session = Depends(get_db),
    service: TrainingService = Depends(get_training_service),
):
    result = service.trigger_sop_training(
        payload.user_ids,
        payload.document_id,
        assigned_by_id=current_user.id,
        notes=payload.notes,
    )
    notif_svc = NotificationService(db)
    for uid in payload.user_ids:
        notif_svc.notify_sop_updated(
            [uid],
            f'Document #{payload.document_id}',
        )
    return {'created': len(result), 'assignments': result}


@router.post('/cgmp/trigger')
def trigger_cgmp_refresher(
    payload: BulkAssignRequest,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: TrainingService = Depends(get_training_service),
):
    return service.trigger_cgmp_refresher(
        payload.user_ids,
        payload.document_id,
        assigned_by_id=current_user.id,
        notes=payload.notes,
    )


@router.post('/complete')
def complete_assignment(
    payload: CompleteRequest,
    current_user: User = Depends(get_current_user),
    service: TrainingService = Depends(get_training_service),
):
    try:
        return service.complete_assignment(payload.assignment_id, current_user.id)
    except (ValueError, PermissionError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get('/trainer/dashboard')
def trainer_dashboard(
    current_user: User = Depends(require_role([UserRole.trainer, UserRole.admin])),
    db: Session = Depends(get_db),
):
    from app.services.trainer_service import TrainerService
    return TrainerService(db).get_trainer_dashboard(current_user.id)


@router.get('/trainer/assignments')
def trainer_assignments(
    current_user: User = Depends(require_role([UserRole.trainer, UserRole.admin])),
    db: Session = Depends(get_db),
):
    from app.services.trainer_service import TrainerService
    return TrainerService(db).get_trainer_assignments(current_user.id)


class AssignmentUpdatePayload(BaseModel):
    status: Optional[str] = None
    due_date: Optional[datetime] = None
    trainer_id: Optional[int] = None


@router.get('/assignments/{assignment_id}')
def get_assignment_endpoint(
    assignment_id: int,
    current_user: User = Depends(get_current_user),
    service: TrainingService = Depends(get_training_service),
):
    a = service.get_assignment(assignment_id)
    if not a:
        raise HTTPException(status_code=404, detail="Assignment not found")
    # Authorization: trainee can only view their own
    if current_user.role == UserRole.trainee and a.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return a


@router.get('/assignments/{assignment_id}/evidence', response_model=list[TrainingEvidenceRead])
def list_assignment_evidence(
    assignment_id: int,
    current_user: User = Depends(get_current_user),
    service: TrainingService = Depends(get_training_service),
):
    assignment = service.get_assignment(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if current_user.role == UserRole.trainee and assignment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    records = service.list_assignment_evidence(assignment_id)
    return [
        TrainingEvidenceRead(
            id=record.id,
            assignment_id=record.assignment_id,
            uploaded_by_id=record.uploaded_by_id,
            label=record.label,
            file_name=record.file_name,
            file_type=record.file_type,
            download_url=f"/training/evidence/{record.id}/download",
            created_at=record.created_at,
        )
        for record in records
    ]


@router.post('/assignments/{assignment_id}/evidence', response_model=TrainingEvidenceRead, status_code=status.HTTP_201_CREATED)
def upload_assignment_evidence(
    assignment_id: int,
    file: UploadFile = File(...),
    label: Optional[str] = Form(default=None),
    current_user: User = Depends(get_current_user),
    service: TrainingService = Depends(get_training_service),
):
    assignment = service.get_assignment(assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if current_user.role == UserRole.trainee and assignment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        record = service.add_assignment_evidence(assignment_id, current_user.id, file, label=label)
    except (ValueError, UploadValidationError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    return TrainingEvidenceRead(
        id=record.id,
        assignment_id=record.assignment_id,
        uploaded_by_id=record.uploaded_by_id,
        label=record.label,
        file_name=record.file_name,
        file_type=record.file_type,
        download_url=f"/training/evidence/{record.id}/download",
        created_at=record.created_at,
    )


@router.get('/evidence/{evidence_id}/download')
def download_assignment_evidence(
    evidence_id: int,
    current_user: User = Depends(get_current_user),
    service: TrainingService = Depends(get_training_service),
):
    record = service.get_evidence(evidence_id)
    if not record:
        raise HTTPException(status_code=404, detail="Evidence not found")

    assignment = service.get_assignment(record.assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if current_user.role == UserRole.trainee and assignment.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")

    from app.storage.file_storage import FileStorageService

    storage = FileStorageService()
    content = storage.get_file_content(record.stored_name)
    # VULN-007 (hardening): sanitize filename for the Content-Disposition header
    # and mark the download as an attachment so browsers never render it inline.
    safe_name = (record.file_name or 'evidence').replace('"', '').replace('\r', '').replace('\n', '')
    return StreamingResponse(
        iter([content]),
        media_type='application/octet-stream',
        headers={'Content-Disposition': f'attachment; filename="{safe_name}"'},
    )


@router.patch('/assignments/{assignment_id}')
def update_assignment_endpoint(
    assignment_id: int,
    payload: AssignmentUpdatePayload,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: TrainingService = Depends(get_training_service),
):
    try:
        return service.update_assignment(assignment_id, **payload.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post('/assignments/{assignment_id}/review')
def review_assignment_endpoint(
    assignment_id: int,
    payload: ReviewAssignmentRequest,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: TrainingService = Depends(get_training_service),
):
    try:
        return service.review_assignment(
            assignment_id,
            current_user.id,
            approved=payload.approved,
            notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete('/assignments/{assignment_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment_endpoint(
    assignment_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: TrainingService = Depends(get_training_service),
):
    success = service.delete_assignment(assignment_id)
    if not success:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return None


# ─── Training Paths CRUD Endpoints ───────────────────────────────────────────

class PathCreatePayload(BaseModel):
    name: str
    description: Optional[str] = ''
    level: Optional[str] = 'Beginner'
    duration_days: Optional[int] = 30
    status: Optional[str] = 'draft'

class ModuleCreatePayload(BaseModel):
    title: str
    description: Optional[str] = ''
    order_index: Optional[int] = 0

class ModuleReorderPayload(BaseModel):
    module_ids: list[int]

class AssignDocumentPayload(BaseModel):
    document_id: int


@router.get('/paths')
def list_training_paths(db: Session = Depends(get_db)):
    """List all training paths dynamically based on database topics and documents."""
    from app.services.response_cache import get_cache, PATHS_CACHE_TTL
    cache = get_cache()
    cached = cache.get('resp:paths:training')
    if cached is not None:
        return cached

    from app.models.topic import Topic
    from app.models.document import Document
    from sqlalchemy import func

    topics = db.query(Topic).all()
    # N+1 fix: one grouped count query instead of one COUNT per topic.
    topic_ids = [t.id for t in topics]
    doc_counts = dict(
        db.query(Document.topic_id, func.count(Document.id))
        .filter(Document.topic_id.in_(topic_ids))
        .group_by(Document.topic_id)
        .all()
    ) if topic_ids else {}
    paths = []
    for t in topics:
        docs_count = doc_counts.get(t.id, 0)
        paths.append({
            'id': t.id,
            'name': t.title,
            'description': getattr(t, 'description', None) or f'Standard operating curriculum for {t.title}',
            'level': 'Intermediate',
            'duration_days': docs_count * 5 if docs_count > 0 else 30,
            'status': 'active',
            'total_modules': docs_count,
            'documents_count': docs_count,
        })
    if not paths:
        # Fallback default path if no topics seeded yet
        docs_count = db.query(Document).count()
        paths.append({
            'id': 1,
            'name': 'Core SOP Learning Path',
            'description': 'Mandatory plant compliance & operating procedures path',
            'level': 'Beginner',
            'duration_days': 30,
            'status': 'active',
            'total_modules': docs_count,
            'documents_count': docs_count,
        })
    cache.set('resp:paths:training', paths, PATHS_CACHE_TTL)
    return paths


@router.post('/paths', status_code=status.HTTP_201_CREATED)
def create_training_path(
    payload: PathCreatePayload,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.trainer])),
    db: Session = Depends(get_db),
):
    from app.models.topic import Topic
    # Default subject_id 1
    new_topic = Topic(
        subject_id=1,
        title=payload.name,
        sequence_order=1,
    )
    db.add(new_topic)
    db.commit()
    db.refresh(new_topic)
    return {
        'id': new_topic.id,
        'name': new_topic.title,
        'description': payload.description,
        'level': payload.level,
        'duration_days': payload.duration_days,
        'status': payload.status,
        'total_modules': 0,
        'documents_count': 0,
    }


@router.put('/paths/{path_id}')
def update_training_path(
    path_id: int,
    payload: PathCreatePayload,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.trainer])),
    db: Session = Depends(get_db),
):
    from app.models.topic import Topic
    t = db.query(Topic).filter(Topic.id == path_id).first()
    if t:
        t.title = payload.name
        db.commit()
    return {
        'id': path_id,
        'name': payload.name,
        'description': payload.description,
        'level': payload.level,
        'duration_days': payload.duration_days,
        'status': payload.status,
        'total_modules': 0,
        'documents_count': 0,
    }


@router.delete('/paths/{path_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_training_path(
    path_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.trainer])),
    db: Session = Depends(get_db),
):
    from app.models.topic import Topic
    t = db.query(Topic).filter(Topic.id == path_id).first()
    if t:
        db.delete(t)
        db.commit()
    return None


@router.get('/paths/{path_id}/modules')
def get_path_modules(path_id: int, db: Session = Depends(get_db)):
    from app.models.document import Document
    docs = db.query(Document).filter(
        (Document.topic_id == path_id) | (path_id == 1)
    ).all()

    modules = []
    for idx, d in enumerate(docs):
        modules.append({
            'id': d.id,
            'title': f"{d.code}: {d.title}",
            'description': f"SOP Document {d.code}",
            'order_index': idx,
            'documents': [{'id': d.id, 'title': d.title, 'code': d.code}],
        })
    return modules


@router.post('/paths/{path_id}/modules', status_code=status.HTTP_201_CREATED)
def add_path_module(
    path_id: int,
    payload: ModuleCreatePayload,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.trainer])),
):
    return {
        'id': payload.order_index + 100,
        'title': payload.title,
        'description': payload.description,
        'order_index': payload.order_index,
        'documents': [],
    }


@router.put('/paths/{path_id}/modules/reorder')
def reorder_path_modules(path_id: int, payload: ModuleReorderPayload):
    return {'message': 'Modules reordered successfully'}


@router.delete('/paths/{path_id}/modules/{module_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_path_module(path_id: int, module_id: int):
    return None


@router.post('/paths/{path_id}/modules/{module_id}/documents')
def assign_document_to_module(path_id: int, module_id: int, payload: AssignDocumentPayload, db: Session = Depends(get_db)):
    from app.models.document import Document
    doc = db.query(Document).filter(Document.id == payload.document_id).first()
    if doc:
        doc.topic_id = path_id
        db.commit()
    return {'message': 'Document assigned to module'}


@router.delete('/paths/{path_id}/modules/{module_id}/documents/{document_id}', status_code=status.HTTP_204_NO_CONTENT)
def remove_document_from_module(path_id: int, module_id: int, document_id: int):
    return None

