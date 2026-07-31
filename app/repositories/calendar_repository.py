from sqlalchemy.orm import Session
from app.models.calendar import CalendarEvent
from datetime import datetime

class CalendarRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, event_id: int):
        return self.db.query(CalendarEvent).filter(CalendarEvent.id == event_id).first()

    def create(self, event: CalendarEvent):
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def update(self, event: CalendarEvent, **kwargs):
        for key, value in kwargs.items():
            setattr(event, key, value)
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def delete(self, event: CalendarEvent):
        self.db.delete(event)
        self.db.commit()

    def list_upcoming(self):
        return self.db.query(CalendarEvent).filter(CalendarEvent.start_time >= datetime.now()).order_by(CalendarEvent.start_time.asc()).all()

    def list_by_trainer(self, trainer_id: int):
        return self.db.query(CalendarEvent).filter(CalendarEvent.trainer_id == trainer_id).order_by(CalendarEvent.start_time.asc()).all()
