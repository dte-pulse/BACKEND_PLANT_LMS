from sqlalchemy import DateTime, ForeignKey, Float, Integer, Boolean, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

class UserMcqAttempt(Base):
    __tablename__ = 'user_mcq_attempts'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    attempt_data: Mapped[dict] = mapped_column(JSON, nullable=True) # stores individual question results
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
