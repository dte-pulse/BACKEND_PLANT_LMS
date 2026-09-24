"""WeaknessAgent — maintains each learner's capability profile.

After every answer it:
1. Records the ChildChunkAttempt + parent-section progress (reuses
   AdaptiveMcqService so dashboards stay consistent).
2. Updates the per-concept mastery row (recency-weighted EMA).
3. Generates an LLM insight when a concept dips into weak territory
   (cost-guarded: only on band changes or every 5 attempts).
4. Rolls the concept scores up to the topic-level UserWeaknessProfile so the
   existing NQ reports keep working unchanged.
5. Invalidates the affected response caches.
"""
import logging

from sqlalchemy.orm import Session

from app.agents.base_agent import BaseAgent, mastery_level_for_score, WEAK_THRESHOLD
from app.models.user_concept_mastery import UserConceptMastery
from app.utils.text_utils import strip_preceding_context

logger = logging.getLogger(__name__)

EMA_CORRECT = 0.25   # score += (100 - score) * EMA_CORRECT on a correct answer
EMA_WRONG = 0.5      # score *= (1 - EMA_WRONG) on a wrong answer


def ema_update(score: float, is_correct: bool) -> float:
    """Pure recency-weighted score update for a single answer."""
    if is_correct:
        return score + (100.0 - score) * EMA_CORRECT
    return score * (1.0 - EMA_WRONG)


class WeaknessAgent(BaseAgent):
    def __init__(self, db: Session):
        super().__init__(db)

    def record_answer(
        self,
        user_id: int,
        chunk,
        question_data: dict,
        selected_option: str,
        is_correct: bool,
        time_taken_seconds: int = 0,
        re_explanation: str | None = None,
    ) -> tuple:
        """Persist the attempt + update mastery. Returns (attempt, mastery_row)."""
        from app.services.adaptive_mcq_service import AdaptiveMcqService

        attempt = AdaptiveMcqService(self.db).record_attempt(
            user_id=user_id,
            chunk=chunk,
            question_data=question_data,
            selected_option=selected_option,
            time_taken_seconds=time_taken_seconds,
            re_explanation=re_explanation,
        )
        mastery = self._update_concept_mastery(user_id, chunk, question_data, is_correct)
        self._sync_topic_weakness(user_id, chunk)

        # Concept/weakness state changed → this user's dashboard + NQ reports stale.
        from app.services.response_cache import invalidate_cached
        invalidate_cached(f'resp:learning:{user_id}:', 'resp:report:')

        return attempt, mastery

    # ── Concept mastery (per child chunk) ───────────────────────────────────

    def _update_concept_mastery(self, user_id: int, chunk, question_data: dict, is_correct: bool) -> UserConceptMastery:
        mastery = self.db.query(UserConceptMastery).filter(
            UserConceptMastery.user_id == user_id,
            UserConceptMastery.child_chunk_id == chunk.id,
        ).first()
        if not mastery:
            mastery = UserConceptMastery(
                user_id=user_id,
                document_id=chunk.document_id,
                topic_id=chunk.topic_id or None,       # NULL not 0 → FK-safe
                parent_chunk_id=chunk.parent_chunk_id or None,  # NULL not 0 → FK-safe
                child_chunk_id=chunk.id,
            )
            # ORM column defaults only apply at INSERT — set them explicitly so
            # the in-memory object is usable before the first flush.
            mastery.score = 0.0
            mastery.attempts = 0
            mastery.correct_attempts = 0
            mastery.consecutive_correct = 0
            self.db.add(mastery)

        mastery.score = ema_update(mastery.score, is_correct)
        if is_correct:
            mastery.consecutive_correct += 1
            mastery.correct_attempts += 1
        else:
            mastery.consecutive_correct = 0

        mastery.attempts += 1
        mastery.last_difficulty = question_data.get('difficulty', 'medium')
        old_level = mastery.mastery_level
        mastery.mastery_level = mastery_level_for_score(mastery.score)
        mastery.needs_review = mastery.score < WEAK_THRESHOLD

        # Insight cost guard: only when weak, ≥2 attempts, and on a band change
        # or every 5th attempt — never per wrong answer.
        if (mastery.score < WEAK_THRESHOLD and mastery.attempts >= 2
                and (old_level != mastery.mastery_level or mastery.attempts % 5 == 0)):
            mastery.insight = self._generate_insight(user_id, chunk, mastery)
        elif mastery.score >= WEAK_THRESHOLD and mastery.insight:
            mastery.insight = None  # review-fix: clear stale insight on recovery

        self.db.commit()
        self.db.refresh(mastery)
        return mastery

    def _generate_insight(self, user_id: int, chunk, mastery: UserConceptMastery) -> str | None:
        content = strip_preceding_context(chunk.content or '')[:1500]
        prompt = f"""A learner is struggling with a concept from a training document. Give a SHORT (2-3 sentence) diagnosis: what they likely misunderstand and exactly what to re-read.

Learner performance on this concept: {mastery.correct_attempts}/{mastery.attempts} correct, current score {mastery.score:.0f}/100, mastery '{mastery.mastery_level}', last question difficulty '{mastery.last_difficulty}'.

Concept content (excerpt):
{content}"""

        text = self.llm().generate_text(prompt, user_id, 'agent_weakness_insight')
        if text:
            return text[:500]
        return None

    # ── Topic roll-up (keeps NQ reports working) ───────────────────────────

    def _sync_topic_weakness(self, user_id: int, chunk):
        """Topic-level score = weighted average of the user's concept scores in
        that topic. Mirrors WeaknessService.record_weakness semantics for
        is_critical so reports don't change behaviour."""
        from sqlalchemy import func
        from app.models.user_weakness_profile import UserWeaknessProfile

        # Match what was stored: real topic_id, or NULL when the chunk had none.
        topic_filter = (UserConceptMastery.topic_id == chunk.topic_id) if chunk.topic_id \
            else UserConceptMastery.topic_id.is_(None)
        row = self.db.query(
            func.avg(UserConceptMastery.score),
            func.sum(UserConceptMastery.attempts),
        ).filter(
            UserConceptMastery.user_id == user_id,
            topic_filter,
        ).first()
        topic_avg = row[0] or 0.0
        total_attempts = row[1] or 0

        profile = self.db.query(UserWeaknessProfile).filter(
            UserWeaknessProfile.user_id == user_id,
            UserWeaknessProfile.topic_id == chunk.topic_id,
        ).first()
        is_critical = topic_avg < 60.0 or (topic_avg < 80.0 and total_attempts >= 3)

        if profile:
            profile.score = round(topic_avg, 2)
            profile.attempt_count = total_attempts
            profile.document_id = chunk.document_id
            profile.is_critical = is_critical
        else:
            self.db.add(UserWeaknessProfile(
                user_id=user_id,
                topic_id=chunk.topic_id,
                document_id=chunk.document_id,
                score=round(topic_avg, 2),
                attempt_count=total_attempts,
                is_critical=is_critical,
            ))
        self.db.commit()
