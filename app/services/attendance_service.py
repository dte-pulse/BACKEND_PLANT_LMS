from sqlalchemy.orm import Session
from app.models.attendance import Attendance
from app.models.calendar import CalendarEvent
from app.models.document import Document
from app.models.training import TrainingAssignment
from app.models.user import User, UserRole
from app.repositories.attendance_repository import AttendanceRepository
from app.schemas.attendance import AttendanceCreate

class AttendanceService:
    def __init__(self, db: Session):
        self.db = db
        self.repository = AttendanceRepository(db)

    def mark_attendance(self, event_id: int, marked_by_id: int, payload: AttendanceCreate):
        attendance = self.repository.get_by_event_and_user(event_id, payload.user_id)
        if attendance:
            return self.repository.update(attendance, is_present=payload.is_present, marked_by_id=marked_by_id)
        else:
            new_attendance = Attendance(
                calendar_event_id=event_id,
                user_id=payload.user_id,
                is_present=payload.is_present,
                marked_by_id=marked_by_id
            )
            return self.repository.create(new_attendance)

    def list_event_attendance(self, event_id: int):
        records = self.repository.list_by_event(event_id)
        if records:
            user_map = {
                user.id: user.full_name or user.employee_code
                for user in self.db.query(User).all()
            }
            return [
                {
                    'id': record.id,
                    'calendar_event_id': record.calendar_event_id,
                    'user_id': record.user_id,
                    'user_name': user_map.get(record.user_id),
                    'is_present': record.is_present,
                    'marked_by_id': record.marked_by_id,
                    'material_used': False,
                    'comments': None,
                    'created_at': record.created_at,
                }
                for record in records
            ]

        event = self.db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()
        if not event:
            return []

        roster_users: list[User] = []
        if event.topic_id is not None:
            docs = self.db.query(Document).filter(Document.topic_id == event.topic_id).all()
            doc_ids = [doc.id for doc in docs]
            if doc_ids:
                trainee_ids = {
                    assignment.user_id
                    for assignment in self.db.query(TrainingAssignment).filter(
                        TrainingAssignment.document_id.in_(doc_ids)
                    ).all()
                }
                if trainee_ids:
                    roster_users = self.db.query(User).filter(User.id.in_(trainee_ids)).all()

        if not roster_users and event.trainer_id:
            roster_users = self.db.query(User).filter(User.role == UserRole.trainee).limit(20).all()

        return [
            {
                'id': None,
                'calendar_event_id': event_id,
                'user_id': user.id,
                'user_name': user.full_name or user.employee_code,
                'is_present': False,
                'marked_by_id': None,
                'material_used': False,
                'comments': None,
                'created_at': None,
            }
            for user in roster_users
        ]
