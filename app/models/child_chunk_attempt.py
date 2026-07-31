from datetime import datetime
from sqlalchemy import ForeignKey, Integer, JSON, String, Text, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.db.session import Base

class ChildChunkAttempt(Base):
    __tablename__ = 'child_chunk_attempts'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    child_chunk_id: Mapped[int] = mapped_column(ForeignKey('chunks.id'), nullable=False, index=True)
    parent_chunk_id: Mapped[int] = mapped_column(ForeignKey('parent_chunks.id'), nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    difficulty_shown: Mapped[str] = mapped_column(String(20), default='easy')
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    options_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    selected_option: Mapped[str | None] = mapped_column(String(5), nullable=True)
    correct_option: Mapped[str | None] = mapped_column(String(5), nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)
    explanation_shown: Mapped[str | None] = mapped_column(Text, nullable=True)
    re_explanation_shown: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_taken_seconds: Mapped[int] = mapped_column(Integer, default=0)
    mcq_id: Mapped[int | None] = mapped_column(ForeignKey('mcq_bank.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
