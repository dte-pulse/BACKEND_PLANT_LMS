from sqlalchemy import ForeignKey, Integer, JSON, Text, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Chunk(Base):
    __tablename__ = 'chunks'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False, index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'), nullable=False, index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey('subjects.id'), nullable=False, index=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    learning_card: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    
    parent_chunk_id: Mapped[int | None] = mapped_column(ForeignKey('parent_chunks.id'), nullable=True, index=True)
    child_index: Mapped[int] = mapped_column(Integer, default=0)
    chunk_type: Mapped[str] = mapped_column(String(20), default='child')
