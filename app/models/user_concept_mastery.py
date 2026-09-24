from datetime import datetime
from sqlalchemy import ForeignKey, Integer, Float, String, Text, Boolean, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.session import Base


class UserConceptMastery(Base):
    """Per-user, per-concept mastery state driving the adaptive learning agents.

    Concept-level capability tracking (a child chunk is the smallest teachable
    unit), rolled up to section (parent chunk), topic, and document. Score is a
    recency-weighted 0-100 estimate updated after every answer; mastery_level
    maps the score to a band the Curriculum/Question agents use to pick the
    difficulty of the next question.
    """
    __tablename__ = 'user_concept_mastery'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False, index=True)
    # topic_id / parent_chunk_id are NULLABLE because legacy flat chunks can
    # carry topic_id=0 (T-4 convention) or parent_chunk_id=None. NULL passes FK
    # constraints, 0 does not — so missing values are stored as NULL.
    topic_id: Mapped[int | None] = mapped_column(ForeignKey('topics.id'), nullable=True, index=True)
    parent_chunk_id: Mapped[int | None] = mapped_column(ForeignKey('parent_chunks.id'), nullable=True, index=True)
    child_chunk_id: Mapped[int] = mapped_column(ForeignKey('chunks.id'), nullable=False, index=True)

    score: Mapped[float] = mapped_column(Float, default=0.0)  # 0-100 recency-weighted estimate
    mastery_level: Mapped[str] = mapped_column(String(20), default='novice')  # novice|learning|proficient|mastered
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    correct_attempts: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_correct: Mapped[int] = mapped_column(Integer, default=0)
    last_difficulty: Mapped[str] = mapped_column(String(20), default='easy')
    insight: Mapped[str | None] = mapped_column(Text, nullable=True)   # LLM diagnosis of what to re-read
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)  # score < 65

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint('user_id', 'child_chunk_id', name='uq_user_child_concept'),)
