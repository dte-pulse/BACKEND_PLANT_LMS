from sqlalchemy import DateTime, ForeignKey, Integer, String, Boolean, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

class Attendance(Base):
    __tablename__ = 'attendance_records'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    calendar_event_id: Mapped[int] = mapped_column(ForeignKey('calendar_events.id'), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    is_present: Mapped[bool] = mapped_column(Boolean, default=False)
    marked_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint('calendar_event_id', 'user_id', name='uq_event_user_attendance'),)
