"""PPWEC — Pulse Professional Workplace Excellence Certification.

Data model for the 18-module behavioural excellence programme:
modules, interactive screens, assessment banks, learner state (the Pulse
Learning Passport), points ledger, badges and the 7-Day Challenge.
"""
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Float,
    String,
    Text,
    JSON,
    Boolean,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PpwecModule(Base):
    """One of the 18 PPWEC modules (§5 of the master content pack)."""
    __tablename__ = 'ppwec_modules'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    module_number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)  # 1..18
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    theme: Mapped[str] = mapped_column(String(255), nullable=False)  # tagline
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=60)
    passing_score: Mapped[float] = mapped_column(Float, default=80.0)  # §16: 80%
    badge_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # micro-badge (§20)
    badge_icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default='draft')  # §29: draft/reviewed/approved/final
    version: Mapped[str] = mapped_column(String(30), default='V1.0')  # e.g. PPWEC-M01-V1.0
    content_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)  # §26
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PpwecScreen(Base):
    """A single learning screen — the §8 standard screen format as a schema.

    `interaction_payload` drives the frontend player: for `interactive` /
    `reflection` / `challenge` screens it contains the full interaction spec
    (options, branching, feedback). Media slots keep future MD/leadership
    videos insertable without redesigning screens (§W).
    """
    __tablename__ = 'ppwec_screens'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=False, index=True)
    screen_number: Mapped[int] = mapped_column(Integer, nullable=False)  # order within module
    section: Mapped[str] = mapped_column(String(50), default='learning')
    # opening | learning | application | assessment | reflection | commitment | challenge | closure
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    on_screen_text: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)   # rich blocks
    voice_over: Mapped[str | None] = mapped_column(Text, nullable=True)               # narration script
    audio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)         # voice-over asset
    caption_url: Mapped[str | None] = mapped_column(String(500), nullable=True)       # captions track
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)               # a11y transcript
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)         # optional embedded video
    visual_direction: Mapped[str | None] = mapped_column(Text, nullable=True)
    interaction_type: Mapped[str] = mapped_column(String(50), default='content')
    # content | click_reveal | choice | multi_choice | drag_sort | classification |
    # branching | simulation | reflection | assessment | challenge | commitment | completion
    interaction_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=True)
    estimated_seconds: Mapped[int] = mapped_column(Integer, default=60)
    pulse_anchor: Mapped[str | None] = mapped_column(String(50), nullable=True)  # P/R/I/C/E price anchor
    md_philosophy: Mapped[bool] = mapped_column(Boolean, default=False)  # touches MD philosophy (§4)

    __table_args__ = (
        UniqueConstraint('module_id', 'screen_number', name='uq_ppwec_screen_order'),
    )


class PpwecQuestion(Base):
    """Assessment question bank (§15–17). Rich two-part feedback per §17."""
    __tablename__ = 'ppwec_questions'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=False, index=True)
    question_type: Mapped[str] = mapped_column(String(50), default='knowledge')
    # knowledge | application | situational_judgement | case_based | decision
    case_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # for case-based
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[dict | list] = mapped_column(JSON, nullable=False)  # [{key, text}]
    correct_option: Mapped[str] = mapped_column(String(20), nullable=False)
    feedback_why: Mapped[str | None] = mapped_column(Text, nullable=True)     # "Why this matters"
    feedback_better: Mapped[str | None] = mapped_column(Text, nullable=True)  # "Better approach"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PpwecUserModuleState(Base):
    """The Pulse Learning Passport row (§19): one per learner per module."""
    __tablename__ = 'ppwec_user_module_state'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=False, index=True)
    screens_completed: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)  # [screen_id,...]
    current_screen_id: Mapped[int | None] = mapped_column(ForeignKey('ppwec_screens.id'), nullable=True)
    completion_percentage: Mapped[float] = mapped_column(Float, default=0.0)
    time_spent_seconds: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(50), default='not_started')
    # not_started | in_progress | assessment_pending | completed
    best_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    badge_awarded: Mapped[bool] = mapped_column(Boolean, default=False)
    points_earned: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_accessed_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint('user_id', 'module_id', name='uq_ppwec_user_module'),
    )


class PpwecAssessmentAttempt(Base):
    """Assessment attempt with the served-question snapshot (randomization integrity, §16)."""
    __tablename__ = 'ppwec_assessment_attempts'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=False, index=True)
    question_ids: Mapped[dict | list] = mapped_column(JSON, nullable=False)  # served order snapshot
    # §16 answer-order randomization: per question, the ORIGINAL option keys in
    # DISPLAY order. Display label = LETTERS[position]; submitted display keys
    # map back through this list at grading time.
    option_orders: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    answers: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {question_id: selected_key}
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    submitted_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PpwecPointsLedger(Base):
    """Gamification scoring (§V.3). Append-only ledger of every award."""
    __tablename__ = 'ppwec_points_ledger'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    activity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # interaction | scenario_decision | simulation | assessment | challenge | module_completion
    points: Mapped[int] = mapped_column(Integer, default=0)
    module_id: Mapped[int | None] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # screen/question id
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PpwecBadge(Base):
    """Badge catalogue (§20): per-module micro-badges + the final credential."""
    __tablename__ = 'ppwec_badges'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)  # final programme credential
    required_modules: Mapped[int] = mapped_column(Integer, default=1)  # modules needed to earn
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PpwecUserBadge(Base):
    """Badge issuance state per learner."""
    __tablename__ = 'ppwec_user_badges'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    badge_id: Mapped[int] = mapped_column(ForeignKey('ppwec_badges.id'), nullable=False, index=True)
    module_id: Mapped[int | None] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=True)
    awarded_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('user_id', 'badge_id', name='uq_ppwec_user_badge'),
    )


class PpwecChallengeProgress(Base):
    """7-Day Challenge (§Q Screen 31): day 1–7 checkboxes, 10 pts/day, max 70."""
    __tablename__ = 'ppwec_challenge_progress'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    module_id: Mapped[int] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=False, index=True)
    days_completed: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)  # [1..7]
    points_awarded: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 7 working days
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'module_id', name='uq_ppwec_challenge'),
    )
