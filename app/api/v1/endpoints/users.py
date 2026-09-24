from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session


from app.api.deps import get_user_service, get_current_user, require_role
from app.db.session import get_db
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserRead
from app.services.user_service import UserService
from app.utils.audit import log_audit_event

router = APIRouter(prefix='/users', tags=['users'])


class UserUpdate(BaseModel):
    full_name: str | None = None
    department: str | None = None
    phone: str | None = None
    designation: str | None = None
    employee_type: str | None = None



# ─── Current user ────────────────────────────────────────────────────────────

@router.get('/me', response_model=UserRead)
def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user


# ─── Admin CRUD ───────────────────────────────────────────────────────────────

@router.get('', response_model=list[UserRead], dependencies=[Depends(require_role([UserRole.admin, UserRole.hod]))])
def list_users(
    department: str | None = None,
    role: str | None = None,
    service: UserService = Depends(get_user_service)
):
    return service.list_users(department=department, role=role)


@router.post('', response_model=UserRead)
def create_user(
    payload: UserCreate,
    request: Request,
    current_user: User = Depends(require_role([UserRole.admin])),
    db: Session = Depends(get_db),
    service: UserService = Depends(get_user_service)
):
    user = service.create_user(payload)
    log_audit_event(
        db=db,
        event='create_user',
        user_id=current_user.id,
        employee_code=current_user.employee_code,
        ip_address=request.client.host if request.client else None,
        details=f"Created user employee_code: {user.employee_code}, role: {user.role.value}"
    )
    return user



@router.get('/{user_id}', response_model=UserRead)
def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in [UserRole.admin, UserRole.hod] and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not authorized')
    from app.repositories.user_repository import UserRepository
    user = UserRepository(db).get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    return user


@router.patch('/{user_id}', response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in [UserRole.admin] and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not authorized')
    from app.repositories.user_repository import UserRepository
    repo = UserRepository(db)
    user = repo.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    updated = repo.update(user, **payload.model_dump(exclude_unset=True))
    from app.services.response_cache import invalidate_cached
    invalidate_cached('resp:report:', f'resp:learning:{user_id}:')
    return updated


@router.post('/{user_id}/deactivate', status_code=status.HTTP_204_NO_CONTENT)
def deactivate_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_role([UserRole.admin])),
    db: Session = Depends(get_db)
):
    from app.repositories.user_repository import UserRepository
    repo = UserRepository(db)
    user = repo.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    repo.deactivate(user_id)
    from app.services.response_cache import invalidate_cached
    invalidate_cached('resp:report:', f'resp:learning:{user_id}:')

    log_audit_event(
        db=db,
        event='deactivate_user',
        user_id=current_user.id,
        employee_code=current_user.employee_code,
        ip_address=request.client.host if request.client else None,
        details=f"Deactivated user employee_code: {user.employee_code}"
    )
    return None


@router.post('/{user_id}/activate', status_code=status.HTTP_204_NO_CONTENT)
def activate_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_role([UserRole.admin])),
    db: Session = Depends(get_db)
):
    from app.repositories.user_repository import UserRepository
    repo = UserRepository(db)
    user = repo.get(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    repo.activate(user_id)
    from app.services.response_cache import invalidate_cached
    invalidate_cached('resp:report:', f'resp:learning:{user_id}:')

    log_audit_event(
        db=db,
        event='activate_user',
        user_id=current_user.id,
        employee_code=current_user.employee_code,
        ip_address=request.client.host if request.client else None,
        details=f"Activated user employee_code: {user.employee_code}"
    )
    return None




@router.get('/{user_id}/training-record')
def get_user_training_record(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin/HOD view of any user's full training record."""
    if current_user.role not in [UserRole.admin, UserRole.hod] and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not authorized')
    from app.services.report_service import ReportService
    return ReportService(db).get_user_training_history(user_id)


@router.get('/{user_id}/progress')
def get_user_progress(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin/HOD view of a user's progress across all documents."""
    if current_user.role not in [UserRole.admin, UserRole.hod] and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not authorized')
    from app.models.user_progress import UserProgress
    progress = db.query(UserProgress).filter(UserProgress.user_id == user_id).all()
    return [
        {
            'document_id': p.document_id,
            'completion_percentage': p.completion_percentage,
            'time_spent_seconds': p.time_spent_seconds,
            'current_chunk_id': p.current_chunk_id,
            'last_accessed_at': p.last_accessed_at,
        }
        for p in progress
    ]


@router.post('/import')
def import_users(
    request: Request,
    file: UploadFile = File(...),
    current_user: User = Depends(require_role([UserRole.admin])),
    db: Session = Depends(get_db),
    service: UserService = Depends(get_user_service)
):
    """Bulk import users from a CSV file."""
    import csv
    import io

    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported.")

    # VULN-007: cap CSV import size (10 MB) — the file is parsed fully in memory.
    from app.core.config import settings as app_settings
    max_csv_bytes = min(10 * 1024 * 1024, int(app_settings.max_upload_bytes))
    content_bytes = file.file.read(max_csv_bytes + 1)
    if len(content_bytes) > max_csv_bytes:
        raise HTTPException(status_code=400, detail=f"CSV file too large. Maximum {max_csv_bytes // (1024 * 1024)} MB.")

    try:
        content = content_bytes.decode('utf-8-sig')
        csv_file = io.StringIO(content)
        reader = csv.DictReader(csv_file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read CSV file: {str(e)}")

    success_count = 0
    errors = []

    # Strip whitespace from headers
    reader.fieldnames = [name.strip().lower() for name in reader.fieldnames] if reader.fieldnames else []

    required_fields = {'employee_code', 'full_name', 'password'}
    missing_fields = required_fields - set(reader.fieldnames)
    if missing_fields:
        raise HTTPException(
            status_code=400,
            detail=f"CSV is missing required columns: {', '.join(missing_fields)}"
        )

    for i, row in enumerate(reader, start=1):
        try:
            # Normalize and clean values
            emp_code = (row.get('employee_code') or '').strip()
            full_name = (row.get('full_name') or '').strip()
            email = (row.get('email') or '').strip() or None
            password = (row.get('password') or '').strip()
            dept = (row.get('department') or '').strip() or None
            role_str = (row.get('role') or 'trainee').strip().lower()
            emp_type = (row.get('employee_type') or 'permanent').strip()

            if not emp_code or not full_name or not password:
                errors.append(f"Row {i}: Missing required values (employee_code, full_name, and password).")
                continue

            # Validate role
            try:
                role = UserRole(role_str)
            except ValueError:
                errors.append(f"Row {i}: Invalid role '{role_str}'. Must be one of admin, hod, trainer, trainee.")
                continue

            # Check if user already exists
            existing_user = service.repository.get_by_employee_code(emp_code)
            if existing_user:
                errors.append(f"Row {i}: Employee code '{emp_code}' already exists.")
                continue

            # Create User
            user_in = UserCreate(
                employee_code=emp_code,
                full_name=full_name,
                email=email,
                password=password,
                department=dept,
                role=role.value,
                employee_type=emp_type
            )
            service.create_user(user_in)
            success_count += 1
        except Exception as e:
            errors.append(f"Row {i}: Unexpected error: {str(e)}")

    log_audit_event(
        db=db,
        event='bulk_import_users',
        user_id=current_user.id,
        employee_code=current_user.employee_code,
        ip_address=request.client.host if request.client else None,
        details=f"Successfully imported {success_count} users. Failed to import {len(errors)} users."
    )

    return {
        "success_count": success_count,
        "failed_count": len(errors),
        "errors": errors
    }


@router.get('/{user_id}/qualification-status')
def get_qualification_status(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Check if trainee has active NQ (Not Qualified) documents.
    If so, is_qualified = False and list the NQ documents.
    """
    if current_user.role not in [UserRole.admin, UserRole.hod] and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not authorized')

    from app.models.user_mcq_attempt import UserMcqAttempt
    from app.models.document import Document
    from sqlalchemy import func

    # Find the latest attempt for each document for this user
    subq = (
        db.query(
            UserMcqAttempt.document_id,
            func.max(UserMcqAttempt.created_at).label('max_created')
        )
        .filter(UserMcqAttempt.user_id == user_id)
        .group_by(UserMcqAttempt.document_id)
        .subquery()
    )

    latest_attempts = (
        db.query(UserMcqAttempt)
        .join(
            subq,
            (UserMcqAttempt.document_id == subq.c.document_id) &
            (UserMcqAttempt.created_at == subq.c.max_created)
        )
        .filter(UserMcqAttempt.user_id == user_id)
        .all()
    )

    nq_documents = []
    for att in latest_attempts:
        if not att.passed:
            doc = db.query(Document).filter(Document.id == att.document_id).first()
            nq_documents.append({
                'document_id': att.document_id,
                'document_code': doc.code if doc else None,
                'document_title': doc.title if doc else None,
                'score': att.score,
                'attempted_at': att.created_at,
                'reason': 'failed_assessment'
            })

    from app.models.training import TrainingAssignment
    pending_induction = db.query(TrainingAssignment).filter(
        TrainingAssignment.user_id == user_id,
        TrainingAssignment.training_type == "induction",
        TrainingAssignment.status != "completed"
    ).all()

    for asgn in pending_induction:
        doc = db.query(Document).filter(Document.id == asgn.document_id).first()
        nq_documents.append({
            'document_id': asgn.document_id,
            'document_code': doc.code if doc else None,
            'document_title': doc.title if doc else "Mandatory Induction SOP",
            'score': 0.0,
            'attempted_at': asgn.created_at,
            'reason': 'induction_pending'
        })

    is_qualified = len(nq_documents) == 0

    restriction = None
    if len(pending_induction) > 0:
        restriction = "Trainee has incomplete induction training assignments. Work-blocked."
    elif not is_qualified:
        restriction = "Trainee failed assessment. HOD cannot allot independent work."

    return {
        'user_id': user_id,
        'is_qualified': is_qualified,
        'nq_documents': nq_documents,
        'restriction': restriction
    }


