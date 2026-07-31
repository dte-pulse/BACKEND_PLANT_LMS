from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.attendance import AttendanceCreate, AttendanceRead, AttendanceRosterRead
from app.services.attendance_service import AttendanceService

router = APIRouter(prefix='/attendance', tags=['attendance'])

def get_attendance_service(db: Session = Depends(get_db)):
    return AttendanceService(db)

@router.get('/event/{event_id}', response_model=list[AttendanceRosterRead])
def get_event_attendance(
    event_id: int,
    current_user: User = Depends(get_current_user),
    service: AttendanceService = Depends(get_attendance_service)
):
    if current_user.role not in ['admin', 'hod', 'trainer']:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to view attendance")
    return service.list_event_attendance(event_id)

@router.post('/event/{event_id}', response_model=AttendanceRead)
def mark_attendance(
    event_id: int,
    payload: AttendanceCreate,
    current_user: User = Depends(get_current_user),
    service: AttendanceService = Depends(get_attendance_service)
):
    if current_user.role not in ['admin', 'hod', 'trainer']:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to mark attendance")
    return service.mark_attendance(event_id, current_user.id, payload)


@router.post('/mark', response_model=AttendanceRead)
def mark_attendance_legacy(
    payload: dict,
    current_user: User = Depends(get_current_user),
    service: AttendanceService = Depends(get_attendance_service)
):
    """Compatibility endpoint for older frontend code paths."""
    if current_user.role not in ['admin', 'hod', 'trainer']:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to mark attendance")
    event_id = payload.get('event_id')
    user_id = payload.get('user_id')
    is_present = payload.get('is_present')
    if event_id is None or user_id is None or is_present is None:
        raise HTTPException(status_code=400, detail='event_id, user_id, and is_present are required')
    return service.mark_attendance(
        int(event_id),
        current_user.id,
        AttendanceCreate(user_id=int(user_id), is_present=bool(is_present))
    )
