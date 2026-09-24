from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Document(Base):
    __tablename__ = 'documents'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    code: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey('topics.id'), nullable=True)
    subject_id: Mapped[int | None] = mapped_column(ForeignKey('subjects.id'), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    sequence_order: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(50), default='draft')
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    file_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    qa_scope: Mapped[str] = mapped_column(String(50), default='doc_strict')
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    mind_map_json: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # E-2: embedding model version this document's vectors were created with.
    # Retrieval compares this to EmbeddingClient.get_model_version() before
    # executing pgvector cosine search; a mismatch means vectors must be
    # re-embedded before they can be searched.
    embedding_model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # P2 #6: how this document was sectioned at ingest — 'structured' (real
    # headings/numbered sections/TOC), 'unstructured' (blind token batching),
    # or None (legacy rows → 'unknown', treated as structured defaults).
    # Drives per-type retrieval thresholds (RELEVANCE_THRESHOLD_BY_TYPE).
    structure_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # I-7: updated whenever the ingestion status changes, so stale-job recovery
    # can detect jobs stuck in a processing state without relying on created_at
    # (which predates re-ingestions of existing documents).
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


