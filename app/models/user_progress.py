from sqlalchemy import DateTime, ForeignKey, Integer, Float, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserProgress(Base):
    __tablename__ = 'user_progress'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'), nullable=False)
    current_chunk_id: Mapped[int | None] = mapped_column(ForeignKey('chunks.id'), nullable=True)
    current_page: Mapped[int] = mapped_column(Integer, default=1)
    completion_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    time_spent_seconds: Mapped[int] = mapped_column(Integer, default=0)
    last_accessed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (UniqueConstraint('user_id', 'document_id', name='uq_user_document_progress'),)
