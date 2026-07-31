from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

class CalendarEvent(Base):
    __tablename__ = 'calendar_events'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey('topics.id'), nullable=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    start_time: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # 1.11 Calendar Engine Additions
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    calendar_type: Mapped[str] = mapped_column(String(50), default='dept_yearly')
    status: Mapped[str] = mapped_column(String(50), default='draft')
    approved_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    is_carried_forward: Mapped[bool] = mapped_column(default=False)
    carry_forward_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Attendance / Execution Metadata
    venue: Mapped[str | None] = mapped_column(String(255), nullable=True)           # specific venue / room
    trainer_remarks: Mapped[str | None] = mapped_column(Text, nullable=True)         # post-session trainer notes
    material_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)     # document code / material ref
    attendance_threshold_breached: Mapped[bool] = mapped_column(default=False)       # True if attendance < 70%
    reschedule_triggered: Mapped[bool] = mapped_column(default=False)               # True if rescheduled due to low attendance

