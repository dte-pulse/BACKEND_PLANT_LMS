from datetime import datetime
from sqlalchemy import ForeignKey, Integer, Float, Boolean, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.db.session import Base

class ParentChunkProgress(Base):
    __tablename__ = 'parent_chunk_progress'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    parent_chunk_id: Mapped[int] = mapped_column(ForeignKey('parent_chunks.id'), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False)
    children_total: Mapped[int] = mapped_column(Integer, default=0)
    children_completed: Mapped[int] = mapped_column(Integer, default=0)
    knowledge_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('user_id', 'parent_chunk_id', name='uq_user_parent_chunk'),
    )
