from sqlalchemy.orm import Session
from app.models.calendar import CalendarEvent
from app.repositories.calendar_repository import CalendarRepository
from app.schemas.calendar import CalendarEventCreate

class CalendarService:
    def __init__(self, db: Session):
        self.repository = CalendarRepository(db)

    def create_event(self, payload: CalendarEventCreate):
        event = CalendarEvent(
            title=payload.title,
            description=payload.description,
            topic_id=payload.topic_id,
            trainer_id=payload.trainer_id,
            start_time=payload.start_time,
            end_time=payload.end_time,
            location=payload.location,
            department=payload.department,
            calendar_type=payload.calendar_type,
            status=payload.status,
            approved_by=payload.approved_by,
            is_carried_forward=payload.is_carried_forward,
            carry_forward_reason=payload.carry_forward_reason,
            actual_date=payload.actual_date
        )
        return self.repository.create(event)

    def list_upcoming_events(self):
        return self.repository.list_upcoming()

    def list_calendar_events(self, department: str | None = None, calendar_type: str | None = None, status: str | None = None):
        query = self.repository.db.query(CalendarEvent)
        if department:
            query = query.filter(CalendarEvent.department == department)
        if calendar_type:
            query = query.filter(CalendarEvent.calendar_type == calendar_type)
        if status:
            query = query.filter(CalendarEvent.status == status)
        return query.order_by(CalendarEvent.start_time.asc()).all()

    def get_event(self, event_id: int):
        return self.repository.get_by_id(event_id)

    def update_event(self, event_id: int, **kwargs):
        event = self.repository.get_by_id(event_id)
        if not event:
            raise ValueError("Event not found")
        return self.repository.update(event, **kwargs)

    def delete_event(self, event_id: int):
        event = self.repository.get_by_id(event_id)
        if event:
            self.repository.delete(event)
            return True
        return False

    def approve_calendar_event(self, event_id: int, hod_id: int):
        event = self.repository.get_by_id(event_id)
        if not event:
            raise ValueError("Event not found")
        return self.repository.update(event, status='approved', approved_by=hod_id)
        
    def flag_attendance_below_70(self, event_id: int):
        from app.models.attendance import Attendance
        db = self.repository.db
        records = db.query(Attendance).filter(Attendance.calendar_event_id == event_id).all()
        if not records:
            return False
        present = sum(1 for r in records if r.is_present)
        percentage = (present / len(records)) * 100
        if percentage < 70.0:
            event = self.repository.get_by_id(event_id)
            if event:
                self.repository.update(event, status='reschedule_required')
            return True
        return False

    def split_session(self, event_id: int, new_start_time, new_end_time):
        event = self.repository.get_by_id(event_id)
        if not event:
            raise ValueError("Event not found")
        new_event = CalendarEvent(
            title=f"{event.title} (Part 2)",
            description=event.description,
            topic_id=event.topic_id,
            trainer_id=event.trainer_id,
            start_time=new_start_time,
            end_time=new_end_time,
            location=event.location,
            department=event.department,
            calendar_type=event.calendar_type,
            status=event.status
        )
        return self.repository.create(new_event)

