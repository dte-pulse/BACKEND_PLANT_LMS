"""PPWEC content governance (§25/§26/§28/§29): the §Y approval gate.

Pilot feedback (5–10 cross-functional employees) and reviewer sign-offs per
gate stage, leading to the Design Freeze that unlocks scale-out to
Modules 2–18. Also: per-module content-validation runs (§X QA checklist).
"""
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PpwecPilotFeedback(Base):
    """§Y pilot survey: engagement / relevance / realism / clarity + rating."""
    __tablename__ = 'ppwec_pilot_feedback'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    engagement: Mapped[int] = mapped_column(Integer, nullable=False)   # 1..5
    relevance: Mapped[int] = mapped_column(Integer, nullable=False)    # 1..5
    realism: Mapped[int] = mapped_column(Integer, nullable=False)      # 1..5
    clarity: Mapped[int] = mapped_column(Integer, nullable=False)      # 1..5
    overall_rating: Mapped[float] = mapped_column(Float, nullable=False)  # avg or 1..5
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    function_tag: Mapped[str | None] = mapped_column(String(100), nullable=True)  # cross-functional spread (§10)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('module_id', 'user_id', name='uq_ppwec_pilot_once'),
    )


class PpwecReviewSignoff(Base):
    """One reviewer decision at one gate stage (§Y sequence).

    Stages: content_owner | md_leadership | ux | pilot | design_freeze
    """
    __tablename__ = 'ppwec_review_signoffs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    reviewer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # approved | changes_requested
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('module_id', 'stage', 'reviewer_id', name='uq_ppwec_signoff_once'),
    )


class PpwecDesignFreeze(Base):
    """§28: the Design Freeze marker — the frozen template Modules 2–18 copy."""
    __tablename__ = 'ppwec_design_freeze'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=False, unique=True, index=True)
    frozen_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    frozen_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # frozen template summary
    frozen_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    unfrozen_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PpwecValidationRun(Base):
    """Result of a §X QA-checklist validation run against a module."""
    __tablename__ = 'ppwec_validation_runs'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=False, index=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    errors: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)   # [{code, message, ...}]
    warnings: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    ran_by_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
