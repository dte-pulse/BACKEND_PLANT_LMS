from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class MCQBank(Base):
    __tablename__ = 'mcq_bank'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False, index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'), nullable=False, index=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey('chunks.id'), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[dict] = mapped_column(JSON, nullable=False)
    correct_option: Mapped[str] = mapped_column(String(20), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[str] = mapped_column(String(30), default='medium')
    type: Mapped[str] = mapped_column(String(30), default='objective')
