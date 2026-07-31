from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.models.user import User, UserRole
from app.schemas.document_assignment import (
    DocumentAssignmentCreate,
    DocumentAssignmentRead,
    DocumentAssignmentUpdate,
)
from app.services.document_assignment_service import DocumentAssignmentService

router = APIRouter(prefix='/document-assignments', tags=['document-assignments'])


def get_assignment_service(db: Session = Depends(get_db)):
    return DocumentAssignmentService(db)


@router.post('', response_model=DocumentAssignmentRead, status_code=status.HTTP_201_CREATED)
def create_document_assignment(
    payload: DocumentAssignmentCreate,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: DocumentAssignmentService = Depends(get_assignment_service)
):
    """Assign a document to a trainee or to a department (Admin/HOD)."""
    if not payload.user_id and not payload.department:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must specify either user_id or department for assignment"
        )
    return service.create_assignment(payload, assigned_by_id=current_user.id)


@router.get('', response_model=list[DocumentAssignmentRead])
def list_document_assignments(
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: DocumentAssignmentService = Depends(get_assignment_service)
):
    """List all document assignments."""
    return service.list_assignments()


@router.get('/my', response_model=list[DocumentAssignmentRead])
def get_my_document_assignments(
    current_user: User = Depends(get_current_user),
    service: DocumentAssignmentService = Depends(get_assignment_service)
):
    """Get document assignments for the current trainee."""
    return service.get_user_assignments(current_user.id)


@router.get('/{assignment_id}', response_model=DocumentAssignmentRead)
def get_document_assignment(
    assignment_id: int,
    current_user: User = Depends(get_current_user),
    service: DocumentAssignmentService = Depends(get_assignment_service)
):
    """Get a single document assignment details."""
    a = service.get_assignment(assignment_id)
    if not a:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    
    # Trainee can only view their own assignments
    if current_user.role == UserRole.trainee and a.user_id != current_user.id:
        if current_user.department != a.department:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")
    return a


@router.patch('/{assignment_id}', response_model=DocumentAssignmentRead)
def update_document_assignment(
    assignment_id: int,
    payload: DocumentAssignmentUpdate,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: DocumentAssignmentService = Depends(get_assignment_service)
):
    """Update assignment details like due date or mandatory status (Admin/HOD)."""
    a = service.update_assignment(assignment_id, payload)
    if not a:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    return a


@router.delete('/{assignment_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_document_assignment(
    assignment_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod])),
    service: DocumentAssignmentService = Depends(get_assignment_service)
):
    """Revoke/delete a document assignment (Admin/HOD)."""
    success = service.delete_assignment(assignment_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignment not found")
    return None
