"""Shared plumbing for the adaptive learning agents.

Mastery model (deterministic, recency-weighted):
- score moves toward 100 on a correct answer, halves on a wrong answer
- mastery band is derived from the score
- difficulty ladder: novice→easy, learning→medium, proficient/mastered→hard
"""
import logging

from app.clients.llm_client import LLMClient

logger = logging.getLogger(__name__)

# ── Mastery bands ────────────────────────────────────────────────────────────
MASTERY_BANDS: list[tuple[float, str]] = [
    (85.0, 'mastered'),
    (65.0, 'proficient'),
    (40.0, 'learning'),
    (0.0, 'novice'),
]

# Score thresholds used by the weakness heatmap.
WEAK_THRESHOLD = 65.0   # below → needs_review / weak area
CRITICAL_THRESHOLD = 40.0  # below → critical weakness

DIFFICULTY_FOR_LEVEL = {
    'novice': 'easy',
    'learning': 'medium',
    'proficient': 'hard',
    'mastered': 'hard',
}


def mastery_level_for_score(score: float) -> str:
    """Map a 0-100 score to a mastery band."""
    for threshold, level in MASTERY_BANDS:
        if score >= threshold:
            return level
    return 'novice'


def difficulty_for_level(level: str) -> str:
    return DIFFICULTY_FOR_LEVEL.get(level, 'medium')


class BaseAgent:
    """Shared LLM plumbing for role-specific agents.

    Subclasses receive the DB session (read-only where possible) and lazily
    build an LLMClient. Every agent degrades gracefully: if the LLM is
    unavailable or fails, deterministic behavior takes over.
    """

    def __init__(self, db=None):
        self.db = db
        self._llm = None

    def llm(self) -> LLMClient | None:
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm
