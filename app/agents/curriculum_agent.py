"""CurriculumAgent — the "adaptivity" brain.

Two responsibilities:
1. plan_question(): for a given concept (child chunk), pick the difficulty and
   question format from the learner's mastery state.
2. recommend_next(): after an answer, decide what the learner should do next —
   reinforce the same concept, move to the weakest sibling in the section,
   advance to the next section, or enter review mode.

Deterministic (no LLM cost) — the LLM is reserved for question content,
diagnosis and study plans.
"""
import logging

from sqlalchemy.orm import Session, defer

from app.agents.base_agent import (
    BaseAgent,
    difficulty_for_level,
    WEAK_THRESHOLD,
)
from app.models.user_concept_mastery import UserConceptMastery
from app.agents.question_generator_agent import FORMAT_BY_DIFFICULTY

logger = logging.getLogger(__name__)


class CurriculumAgent(BaseAgent):
    def __init__(self, db: Session):
        super().__init__(db)

    # ── Question planning ───────────────────────────────────────────────────

    def plan_question(self, user_id: int, chunk) -> dict:
        mastery = self.db.query(UserConceptMastery).filter(
            UserConceptMastery.user_id == user_id,
            UserConceptMastery.child_chunk_id == chunk.id,
        ).first()

        score = mastery.score if mastery else 0.0
        level = mastery.mastery_level if mastery else 'novice'
        attempts = mastery.attempts if mastery else 0

        decision = self._decide(attempts, score, level)
        return {
            **decision,
            'target_summary': f'concept {chunk.id} (page {chunk.page_no}) at {decision["difficulty"]}/{decision["format"]}',
        }

    @staticmethod
    def _decide(attempts: int, score: float, level: str) -> dict:
        """Pure decision logic: (difficulty, format, reason) from mastery state."""
        # First exposure is always easy; otherwise escalate with mastery.
        difficulty = 'easy' if attempts == 0 else difficulty_for_level(level)

        # Vary the format across attempts (recall → application → scenario).
        pool = FORMAT_BY_DIFFICULTY[difficulty]
        prefer_format = pool[attempts % len(pool)]
        return {
            'difficulty': difficulty,
            'format': prefer_format,
            'reason': CurriculumAgent._plan_reason(attempts, score, level, difficulty),
        }

    @staticmethod
    def _plan_reason(attempts: int, score: float, level: str, difficulty: str) -> str:
        if attempts == 0:
            return 'First exposure to this concept — starting with an easy recall question.'
        if score < WEAK_THRESHOLD:
            return (f'Score {score:.0f} is below the mastery threshold — keeping questions '
                    f'at {difficulty} difficulty until the concept strengthens.')
        return f'Concept is {level} (score {score:.0f}) — escalating to {difficulty} difficulty.'

    # ── Next-step recommendation ────────────────────────────────────────────

    def recommend_next(self, user_id: int, chunk, is_correct: bool) -> dict:
        """Rule-based next action after an answer on `chunk`."""
        mastery = self.db.query(UserConceptMastery).filter(
            UserConceptMastery.user_id == user_id,
            UserConceptMastery.child_chunk_id == chunk.id,
        ).first()
        score = mastery.score if mastery else 0.0

        # 1. Concept still weak → reinforce it with a fresh question.
        if score < WEAK_THRESHOLD:
            return {
                'action': 'reinforce',
                'target_chunk_id': chunk.id,
                'reason': (f'Your score on this concept is {score:.0f} — below the '
                           f'{WEAK_THRESHOLD:.0f} threshold. Let\'s reinforce it with another question.'),
            }

        # 2. Weakest sibling in the same section (batch-fetch mastery rows).
        from app.models.chunk import Chunk
        siblings = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
            Chunk.parent_chunk_id == chunk.parent_chunk_id,
        ).order_by(Chunk.child_index).all()
        sibling_ids = [s.id for s in siblings]
        mastery_by_child: dict[int, UserConceptMastery] = {}
        if sibling_ids:
            for m in self.db.query(UserConceptMastery).filter(
                UserConceptMastery.user_id == user_id,
                UserConceptMastery.child_chunk_id.in_(sibling_ids),
            ).all():
                mastery_by_child[m.child_chunk_id] = m

        weakest = None
        weakest_score = 101.0
        unconquered = []
        for s in siblings:
            if s.id == chunk.id:
                continue
            m = mastery_by_child.get(s.id)
            s_score = m.score if m else 0.0
            if s_score < weakest_score:
                weakest, weakest_score = s, s_score
            if not m or m.mastery_level in ('novice', 'learning') or s_score < WEAK_THRESHOLD:
                unconquered.append(s)

        if weakest is not None:
            return {
                'action': 'review_section',
                'target_chunk_id': weakest.id,
                'reason': (f'The weakest concept in this section is on page {weakest.page_no} '
                           f'(score {weakest_score:.0f}). Study it next.'),
            }
        if unconquered:
            nxt = unconquered[0]
            return {
                'action': 'continue_section',
                'target_chunk_id': nxt.id,
                'reason': f'Next concept in this section is on page {nxt.page_no}.',
            }

        # 3. Section mastered → advance to the next section's first child.
        from app.services.adaptive_mcq_service import AdaptiveMcqService
        if chunk.parent_chunk_id and AdaptiveMcqService(self.db).is_parent_passed(user_id, chunk.parent_chunk_id):
            from app.models.parent_chunk import ParentChunk
            current_parent = self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
                ParentChunk.id == chunk.parent_chunk_id,
            ).first()
            if current_parent:
                next_parent = self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
                    ParentChunk.document_id == chunk.document_id,
                    ParentChunk.section_index > current_parent.section_index,
                ).order_by(ParentChunk.section_index).first()
                if next_parent:
                    first_child = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
                        Chunk.parent_chunk_id == next_parent.id,
                    ).order_by(Chunk.child_index).first()
                    if first_child:
                        return {
                            'action': 'advance',
                            'target_chunk_id': first_child.id,
                            'reason': f'Section passed — moving to the next section (page {first_child.page_no}).',
                        }

        # 4. Everything conquered → review mode with hard questions.
        return {
            'action': 'review_mode',
            'target_chunk_id': chunk.id,
            'reason': 'Document section mastered — review with harder questions to lock it in.',
        }
