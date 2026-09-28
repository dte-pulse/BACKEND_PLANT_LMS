"""Gamification — coins, streaks and the monthly leaderboard.

Rewards every meaningful learning event (chunk completion, MCQ pass,
assignment completion, PPWEC screens/assessments/challenge) with coins.
Coins power the leaderboard; streaks reward daily consistency.
"""
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CoinLedger(Base):
    """Append-only coin ledger. One row per award.

    reference_type + reference_id deduplicate awards (checked by the service
    before insert); activity_type drives the wallet's coin breakdown.
    """
    __tablename__ = 'coin_ledger'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, index=True)
    activity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # chunk_complete | mcq_pass | mcq_fail | assignment_complete | daily_login |
    # ppwec_interaction | ppwec_assessment | ppwec_challenge_day | ppwec_module_complete |
    # streak_bonus
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # coins awarded
    module_id: Mapped[int | None] = mapped_column(ForeignKey('ppwec_modules.id'), nullable=True)
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # Dedup guard: one award per (user, activity, reference). Partial-ish
        # semantics: NULL reference rows are app-guarded, not DB-guarded.
        Index('uq_coin_dedup', 'user_id', 'activity_type', 'reference_type', 'reference_id',
              unique=True),
    )


class UserStreak(Base):
    """One row per user: current streak, longest streak, last active day.

    `week_activity` holds the last 7 ISO weekdays (1=Mon..7=Sun) the user earned
    coins on, most recent first — drives the M..S week strip in the UI.
    """
    __tablename__ = 'user_streaks'

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False, unique=True, index=True)
    current_streak: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')
    longest_streak: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')
    total_coins: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')
    last_active_date: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    week_activity: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)  # [1..7]
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
