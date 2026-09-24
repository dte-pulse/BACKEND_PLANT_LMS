from sqlalchemy import ForeignKey, Integer, Text, String
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.db.session import Base
from app.utils.constants import EMBEDDING_DIM  # E-3: single source of truth


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
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    
    parent_chunk_id: Mapped[int | None] = mapped_column(ForeignKey('parent_chunks.id'), nullable=True, index=True)
    child_index: Mapped[int] = mapped_column(Integer, default=0)
    chunk_type: Mapped[str] = mapped_column(String(20), default='child')
    stable_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Upgrade 2 — Contextual Retrieval: LLM-written situating header ("This chunk
    # is from section X of <doc>, covering …"). Prepended to the chunk text for
    # embedding, BM25 and answer prompts — never a substitute for the content.
    contextual_header: Mapped[str | None] = mapped_column(Text, nullable=True)

