from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

class UserQaSession(Base):
    __tablename__ = 'user_qa_sessions'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    source_chunk_id: Mapped[int | None] = mapped_column(ForeignKey('chunks.id'), nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
