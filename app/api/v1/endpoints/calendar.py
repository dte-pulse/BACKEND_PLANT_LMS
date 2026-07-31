from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
from pydantic import BaseModel

from app.api.deps import get_current_user, require_role
from app.db.session import get_db
from app.models.user import User, UserRole
from app.schemas.calendar import CalendarEventCreate, CalendarEventRead
from app.services.calendar_service import CalendarService

router = APIRouter(prefix='/calendar', tags=['calendar'])

def get_calendar_service(db: Session = Depends(get_db)):
    return CalendarService(db)

class CalendarEventUpdatePayload(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    location: Optional[str] = None
    department: Optional[str] = None
    calendar_type: Optional[str] = None
    status: Optional[str] = None
    is_carried_forward: Optional[bool] = None
    carry_forward_reason: Optional[str] = None
    actual_date: Optional[datetime] = None
    venue: Optional[str] = None
    trainer_remarks: Optional[str] = None
    material_ref: Optional[str] = None

class SplitSessionPayload(BaseModel):
    new_start_time: datetime
    new_end_time: datetime

@router.get('/events', response_model=list[CalendarEventRead])
def get_events(
    department: Optional[str] = None,
    calendar_type: Optional[str] = None,
    status: Optional[str] = None,
    service: CalendarService = Depends(get_calendar_service)
):
    """
    List events. Supports filtering by department (yearly planning),
    calendar_type (dept_yearly or qa_annual), and status.
    """
    return service.list_calendar_events(
        department=department,
        calendar_type=calendar_type,
        status=status
    )


@router.get('/upcoming', response_model=list[CalendarEventRead])
def get_upcoming_events(
    current_user: User = Depends(get_current_user),
    service: CalendarService = Depends(get_calendar_service)
):
    """Compatibility endpoint for dashboards and attendance pages still requesting upcoming sessions."""
    return service.list_upcoming_events()

@router.post('/events', response_model=CalendarEventRead, status_code=status.HTTP_201_CREATED)
def create_event(
    payload: CalendarEventCreate,
    current_user: User = Depends(get_current_user),
    service: CalendarService = Depends(get_calendar_service)
):
    if current_user.role not in [UserRole.admin, UserRole.hod, UserRole.trainer]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to create events")
    return service.create_event(payload)

@router.patch('/events/{event_id}', response_model=CalendarEventRead)
def update_event(
    event_id: int,
    payload: CalendarEventUpdatePayload,
    current_user: User = Depends(get_current_user),
    service: CalendarService = Depends(get_calendar_service)
):
    """
    Update a calendar event. Supports carry forward justification, actual date tracking, or rescheduling.
    """
    if current_user.role not in [UserRole.admin, UserRole.hod, UserRole.trainer]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to update events")
    try:
        return service.update_event(event_id, **payload.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

@router.post('/events/{event_id}/approve', response_model=CalendarEventRead)
def approve_event(
    event_id: int,
    current_user: User = Depends(require_role([UserRole.hod, UserRole.admin])),
    service: CalendarService = Depends(get_calendar_service)
):
    """
    HOD/Admin approval workflow for planned calendar events.
    """
    try:
        return service.approve_calendar_event(event_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

@router.post('/events/{event_id}/reschedule-check')
def check_reschedule(
    event_id: int,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: CalendarService = Depends(get_calendar_service)
):
    """
    Check if attendance is below 70% and flag for rescheduling if so.
    Also persists attendance_threshold_breached and reschedule_triggered on the event.
    """
    flagged = service.flag_attendance_below_70(event_id)
    if flagged:
        service.update_event(event_id, attendance_threshold_breached=True, reschedule_triggered=True)
    return {"event_id": event_id, "reschedule_required": flagged}

@router.post('/events/{event_id}/split', response_model=CalendarEventRead)
def split_event_session(
    event_id: int,
    payload: SplitSessionPayload,
    current_user: User = Depends(require_role([UserRole.admin, UserRole.hod, UserRole.trainer])),
    service: CalendarService = Depends(get_calendar_service)
):
    """
    Split a participant session into multiple sessions if required.
    """
    try:
        return service.split_session(event_id, payload.new_start_time, payload.new_end_time)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

@router.delete('/events/{event_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: int,
    current_user: User = Depends(get_current_user),
    service: CalendarService = Depends(get_calendar_service)
):
    if current_user.role not in [UserRole.admin, UserRole.hod, UserRole.trainer]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete events")
    success = service.delete_event(event_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Event not found')
    return None
