from sqlalchemy import ForeignKey, Integer, Text, String
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.utils.constants import EMBEDDING_DIM  # E-3: single source of truth

class ParentChunk(Base):
    __tablename__ = 'parent_chunks'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey('documents.id'), nullable=False, index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey('topics.id'), nullable=False)
    subject_id: Mapped[int] = mapped_column(ForeignKey('subjects.id'), nullable=False)
    section_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    page_start: Mapped[int] = mapped_column(Integer, default=1)
    page_end: Mapped[int] = mapped_column(Integer, default=1)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    stable_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

