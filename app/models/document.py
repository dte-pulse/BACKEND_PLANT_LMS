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
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


