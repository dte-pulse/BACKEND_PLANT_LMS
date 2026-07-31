from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TrainingAssignment(Base):
    __tablename__ = 'training_assignments'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=True) # Optional for external
    training_type: Mapped[str] = mapped_column(String(100), nullable=False) # 'induction', 'ojt', 'external', 'sop', 'cgmp'
    status: Mapped[str] = mapped_column(String(50), default='assigned') # assigned, pending_verification, completed
    due_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Operational fields
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True) # For OJT
    verified_by_trainer: Mapped[bool] = mapped_column(default=False)
    certificate_url: Mapped[str | None] = mapped_column(String(500), nullable=True) # For External
    assigned_by_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_provider: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_venue: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_duration_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
