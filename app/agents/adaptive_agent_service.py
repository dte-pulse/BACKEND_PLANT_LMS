"""AdaptiveAgentService — orchestrator that wires the agents together.

Flow for a learning session:

    question: CurriculumAgent.plan_question → QuestionGeneratorAgent.generate
              (+ learner's current mastery state)
    answer:   EvaluatorAgent.evaluate → WeaknessAgent.record_answer
              (attempt + concept mastery + topic roll-up + cache invalidation)
              → CurriculumAgent.recommend_next → progress sync

The response contract of the legacy adaptive path is preserved (all existing
fields), with new agent fields added alongside: mastery, insight, diagnosis,
recommendation, agents.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session, defer

from app.agents.curriculum_agent import CurriculumAgent
from app.agents.evaluator_agent import EvaluatorAgent
from app.agents.question_generator_agent import QuestionGeneratorAgent
from app.agents.recommender_agent import RecommenderAgent
from app.agents.weakness_agent import WeaknessAgent
from app.models.user_concept_mastery import UserConceptMastery

logger = logging.getLogger(__name__)


class AdaptiveAgentService:
    def __init__(self, db: Session, user_id: int = 0):
        self.db = db
        self.user_id = user_id
        self.curriculum = CurriculumAgent(db)
        self.question_generator = QuestionGeneratorAgent(db)
        self.evaluator = EvaluatorAgent(db)
        self.weakness = WeaknessAgent(db)
        self.recommender = RecommenderAgent(db)

    # ── Question ────────────────────────────────────────────────────────────

    def get_next_question(self, user_id: int, chunk) -> dict:
        """Full agent-driven question for a concept, with the learner's mastery
        state and the agents' decision trace."""
        from app.clients.langfuse_client import langfuse_observation

        chunk_id = getattr(chunk, 'id', None)
        document_id = getattr(chunk, 'document_id', None)

        with langfuse_observation(
            name='adaptive-question',
            as_type='agent',
            user_id=user_id or None,
            tags=['learning', 'adaptive'],
            metadata={'chunk_id': chunk_id, 'document_id': document_id, 'feature': 'adaptive-learning'},
        ):
            with langfuse_observation(
                name='curriculum-decision',
                as_type='agent',
                metadata={'chunk_id': chunk_id, 'document_id': document_id},
            ):
                plan = self.curriculum.plan_question(user_id, chunk)

            from app.models.child_chunk_attempt import ChildChunkAttempt
            shown_mcq_ids = [
                a.mcq_id for a in self.db.query(ChildChunkAttempt).filter(
                    ChildChunkAttempt.user_id == user_id,
                    ChildChunkAttempt.child_chunk_id == chunk.id,
                    ChildChunkAttempt.mcq_id.isnot(None),
                ).all() if a.mcq_id
            ]

            with langfuse_observation(
                name='question-generator',
                as_type='agent',
                metadata={'chunk_id': chunk_id, 'document_id': document_id, 'source': 'bank' if shown_mcq_ids else 'dynamic'},
            ):
                question = self.question_generator.generate(
                    user_id=user_id,
                    chunk=chunk,
                    difficulty=plan['difficulty'],
                    prefer_format=plan['format'],
                    exclude_mcq_ids=shown_mcq_ids,
                )

            return {
                **question,
                'chunk_id': chunk.id,
                'mastery': self._mastery_dict(user_id, chunk),
                'plan': {
                    'difficulty': plan['difficulty'],
                    'format': plan['format'],
                    'reason': plan['reason'],
                },
                'agents': {
                    'curriculum': {
                        'agent': 'CurriculumAgent',
                        'decision': plan['reason'],
                        'target': plan['target_summary'],
                    },
                    'question_generator': {
                        'agent': 'QuestionGeneratorAgent',
                        'source': 'dynamic' if question['is_dynamic'] else 'bank',
                        'difficulty': plan['difficulty'],
                        'format': question.get('format', plan['format']),
                    },
                },
            }

    def _mastery_dict(self, user_id: int, chunk) -> dict:
        row = self.db.query(UserConceptMastery).filter(
            UserConceptMastery.user_id == user_id,
            UserConceptMastery.child_chunk_id == chunk.id,
        ).first()
        if not row:
            return {
                'score': 0.0, 'mastery_level': 'novice', 'attempts': 0,
                'correct_attempts': 0, 'consecutive_correct': 0,
                'needs_review': False, 'insight': None,
            }
        return {
            'score': round(row.score, 1),
            'mastery_level': row.mastery_level,
            'attempts': row.attempts,
            'correct_attempts': row.correct_attempts,
            'consecutive_correct': row.consecutive_correct,
            'needs_review': row.needs_review,
            'insight': row.insight,
        }

    # ── Answer ──────────────────────────────────────────────────────────────

    def submit_answer(
        self,
        user_id: int,
        chunk,
        question_data: dict,
        selected_option: str,
        time_taken_seconds: int = 0,
    ) -> dict:
        """Evaluate the answer, update the capability profile, sync progress and
        recommend the next step."""
        from app.clients.langfuse_client import langfuse_observation, score_trace
        from app.services.adaptive_mcq_service import AdaptiveMcqService

        adaptive = AdaptiveMcqService(self.db)
        chunk_id = getattr(chunk, 'id', None)
        document_id = getattr(chunk, 'document_id', None)

        with langfuse_observation(
            name='adaptive-answer',
            as_type='agent',
            user_id=user_id or None,
            tags=['learning', 'adaptive'],
            metadata={'chunk_id': chunk_id, 'document_id': document_id, 'feature': 'adaptive-learning'},
        ) as trace:
            # 1. Evaluator agent grades + diagnoses.
            with langfuse_observation(
                name='evaluator',
                as_type='agent',
                metadata={'chunk_id': chunk_id, 'document_id': document_id},
            ):
                eval_result = self.evaluator.evaluate(
                    user_id, chunk, question_data, selected_option, time_taken_seconds
                )

            # 2. Weakness agent records the attempt + updates mastery/profile.
            with langfuse_observation(
                name='weakness-update',
                as_type='agent',
                metadata={'chunk_id': chunk_id, 'document_id': document_id},
            ):
                attempt, _ = self.weakness.record_answer(
                    user_id=user_id,
                    chunk=chunk,
                    question_data=question_data,
                    selected_option=selected_option,
                    is_correct=eval_result['is_correct'],
                    time_taken_seconds=time_taken_seconds,
                    re_explanation=eval_result['re_explanation'],
                )
            mastery_state = self._mastery_dict(user_id, chunk)  # single read, reused below

            # 3. Progress sync (UserProgress + assignment completion).
            self._sync_user_progress(user_id, chunk, time_taken_seconds)

            # 3b. Gamification: coins for the attempt + chunk completion.
            self._award_learning_coins(user_id, chunk, eval_result['is_correct'], attempt)

            # 4. Curriculum agent recommends the next step.
            with langfuse_observation(
                name='curriculum-next-step',
                as_type='agent',
                metadata={'chunk_id': chunk_id, 'document_id': document_id},
            ):
                recommendation = self.curriculum.recommend_next(user_id, chunk, eval_result['is_correct'])

            knowledge_score = adaptive.get_child_knowledge_score(user_id, chunk.id)
            child_passed = knowledge_score >= 80.0

            next_child, parent_completed, document_completed = self._compute_next_step(
                user_id, chunk, child_passed
            )

            # Eval: every answer is scored on correctness → learning-quality dashboards.
            score_trace(
                trace,
                'answer-correctness',
                1.0 if eval_result['is_correct'] else 0.0,
                'BOOLEAN',
                comment='Correct answer' if eval_result['is_correct'] else 'Wrong answer',
            )

            return {
                'is_correct': eval_result['is_correct'],
                'correct_option': eval_result['correct_option'],
                'explanation': eval_result['explanation'],
                're_explanation': eval_result['re_explanation'],
                'diagnosis': eval_result['diagnosis'],
                'child_passed': child_passed,
                'knowledge_score': knowledge_score,
                'attempt_number': attempt.attempt_number,
                'difficulty': attempt.difficulty_shown,
                'next_child': next_child,
                'parent_completed': parent_completed,
                'document_completed': document_completed,
                # ── New agent fields ──
                'mastery': mastery_state,
                'recommendation': recommendation,
                'agents': {
                    'evaluator': {
                        'agent': 'EvaluatorAgent',
                        'diagnosis': eval_result['diagnosis'],
                    },
                    'weakness': {
                        'agent': 'WeaknessAgent',
                        'concept_id': chunk.id,
                        'new_level': mastery_state['mastery_level'],
                    },
                    'curriculum': {
                        'agent': 'CurriculumAgent',
                        'next_action': recommendation['action'],
                        'reason': recommendation['reason'],
                    },
                },
            }

    def _award_learning_coins(self, user_id: int, chunk, is_correct: bool, attempt) -> None:
        """Best-effort coin awards for the adaptive answer loop.

        Per-attempt coins reference the attempt id (dedup-safe across retries);
        a child-chunk completion bonus fires once per (user, chunk).
        """
        try:
            from app.services.gamification_service import GamificationService
            gam = GamificationService(self.db)
            event = 'mcq_passed' if is_correct else 'mcq_failed'
            attempt_id = str(getattr(attempt, 'id', None) or f"{user_id}-{getattr(chunk, 'id', None)}")
            gam.notify_event(user_id, event, attempt_id=attempt_id,
                             document_id=getattr(chunk, 'document_id', None))
            if getattr(attempt, 'attempt_number', None) == 1 and is_correct:
                gam.notify_event(user_id, 'chunk_completed', chunk_id=str(chunk.id),
                                 document_id=getattr(chunk, 'document_id', None))
        except Exception:  # noqa: BLE001 — rewards must never break learning
            pass

    # ── Progress sync (ported from LearningSessionService.submit_child_answer) ──

    def _sync_user_progress(self, user_id: int, chunk, time_taken_seconds: int):
        from app.models.chunk import Chunk
        from app.models.parent_chunk import ParentChunk
        from app.models.user_progress import UserProgress
        from app.models.training import TrainingAssignment
        from app.services.adaptive_mcq_service import AdaptiveMcqService

        adaptive = AdaptiveMcqService(self.db)
        doc_chunks = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
            Chunk.document_id == chunk.document_id,
        ).all()
        doc_parents = self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
            ParentChunk.document_id == chunk.document_id,
        ).all()

        total_children_count = len(doc_chunks)
        passed_children_count = 0
        for p in doc_parents:
            if adaptive.is_parent_passed(user_id, p.id):
                passed_children_count += self.db.query(Chunk).filter(
                    Chunk.parent_chunk_id == p.id,
                ).count()

        completion_pct = round((passed_children_count / total_children_count * 100), 2) if total_children_count else 0.0

        progress = self.db.query(UserProgress).filter(
            UserProgress.user_id == user_id,
            UserProgress.document_id == chunk.document_id,
        ).first()
        if not progress:
            progress = UserProgress(
                user_id=user_id,
                document_id=chunk.document_id,
                topic_id=chunk.topic_id or 0,
                current_chunk_id=chunk.id,
                current_page=chunk.page_no,
                completion_percentage=completion_pct,
                time_spent_seconds=time_taken_seconds,
                last_accessed_at=datetime.now(timezone.utc),
            )
            self.db.add(progress)
        else:
            progress.completion_percentage = completion_pct
            progress.current_chunk_id = chunk.id
            progress.current_page = chunk.page_no
            progress.time_spent_seconds = (progress.time_spent_seconds or 0) + time_taken_seconds
            progress.last_accessed_at = datetime.now(timezone.utc)
        self.db.commit()

        # Assignment completes automatically at 100%.
        if completion_pct >= 100.0:
            assignment = self.db.query(TrainingAssignment).filter(
                TrainingAssignment.user_id == user_id,
                TrainingAssignment.document_id == chunk.document_id,
                TrainingAssignment.status != 'completed',
            ).first()
            if assignment:
                assignment.status = 'completed'
                self.db.commit()
                from app.services.response_cache import invalidate_cached
                invalidate_cached('resp:report:', f'resp:learning:{user_id}:')

    def _compute_next_step(self, user_id: int, chunk, child_passed: bool):
        from app.models.chunk import Chunk
        from app.models.parent_chunk import ParentChunk

        if not child_passed:
            return None, False, False

        current_parent = self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
            ParentChunk.id == chunk.parent_chunk_id,
        ).first()
        if not current_parent:
            return None, False, False

        next_parent = self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
            ParentChunk.document_id == chunk.document_id,
            ParentChunk.section_index > current_parent.section_index,
        ).order_by(ParentChunk.section_index).first()
        if not next_parent:
            return None, True, True

        first_child = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
            Chunk.parent_chunk_id == next_parent.id,
        ).order_by(Chunk.child_index).first()
        if not first_child:
            return None, True, False
        return self._chunk_to_dict(first_child), True, False

    @staticmethod
    def _chunk_to_dict(chunk) -> dict:
        return {
            'id': chunk.id,
            'parent_chunk_id': chunk.parent_chunk_id,
            'child_index': chunk.child_index,
            'chunk_index': chunk.chunk_index,
            'page_no': chunk.page_no,
            'content': chunk.content,
            'learning_card': chunk.learning_card,
            'token_count': chunk.token_count,
        }
