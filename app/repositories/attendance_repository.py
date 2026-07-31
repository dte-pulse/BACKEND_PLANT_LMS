from sqlalchemy.orm import Session
from app.models.attendance import Attendance

class AttendanceRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_event_and_user(self, event_id: int, user_id: int):
        return self.db.query(Attendance).filter(
            Attendance.calendar_event_id == event_id,
            Attendance.user_id == user_id
        ).first()

    def create(self, attendance: Attendance):
        self.db.add(attendance)
        self.db.commit()
        self.db.refresh(attendance)
        return attendance

    def update(self, attendance: Attendance, **kwargs):
        for key, value in kwargs.items():
            setattr(attendance, key, value)
        self.db.add(attendance)
        self.db.commit()
        self.db.refresh(attendance)
        return attendance

    def list_by_event(self, event_id: int):
        return self.db.query(Attendance).filter(Attendance.calendar_event_id == event_id).all()
