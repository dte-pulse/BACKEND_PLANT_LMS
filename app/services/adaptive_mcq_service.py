"""
Adaptive MCQ Service
====================
Handles the full adaptive question-answer loop for child chunks:
- Determines appropriate difficulty based on attempt history
- Tries MCQ bank cache first (pre-generated), falls back to dynamic Gemini generation
- Tracks attempt history to compute child_chunk knowledge score
- 80% threshold to pass a child chunk (score = correct / total_attempts * 100)
"""
import json
import logging
import random
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from app.models.chunk import Chunk
from app.models.mcq import MCQBank
from app.models.child_chunk_attempt import ChildChunkAttempt
from app.models.parent_chunk import ParentChunk
from app.models.parent_chunk_progress import ParentChunkProgress

logger = logging.getLogger(__name__)

PASS_THRESHOLD = 80.0  # percent
DIFFICULTY_MAP = {1: 'easy', 2: 'medium', 3: 'hard'}  # attempt_number -> difficulty

class AdaptiveMcqService:
    def __init__(self, db: Session):
        self.db = db

    def get_child_attempt_count(self, user_id: int, child_chunk_id: int) -> int:
        """How many attempts has this user made on this child chunk?"""
        return self.db.query(ChildChunkAttempt).filter(
            ChildChunkAttempt.user_id == user_id,
            ChildChunkAttempt.child_chunk_id == child_chunk_id
        ).count()

    def get_child_knowledge_score(self, user_id: int, child_chunk_id: int) -> float:
        """Returns 0-100 score: (correct_attempts / total_attempts) * 100"""
        attempts = self.db.query(ChildChunkAttempt).filter(
            ChildChunkAttempt.user_id == user_id,
            ChildChunkAttempt.child_chunk_id == child_chunk_id
        ).all()
        if not attempts:
            return 0.0
        correct = sum(1 for a in attempts if a.is_correct)
        return round((correct / len(attempts)) * 100, 2)

    def is_child_passed(self, user_id: int, child_chunk_id: int) -> bool:
        """Returns True if user has achieved >= 80% on this child chunk"""
        score = self.get_child_knowledge_score(user_id, child_chunk_id)
        return score >= PASS_THRESHOLD

    def get_difficulty_for_next_attempt(self, user_id: int, child_chunk_id: int) -> str:
        """Escalates difficulty: attempt 1=easy, 2=medium, 3+=hard"""
        count = self.get_child_attempt_count(user_id, child_chunk_id)
        return DIFFICULTY_MAP.get(count + 1, 'hard')

    def get_next_question(self, user_id: int, chunk: Chunk) -> dict:
        """
        Get the next question for this user on this child chunk.
        1. Determine difficulty based on attempt history
        2. Try MCQ bank first (filter by difficulty, exclude already-shown MCQ IDs)
        3. If no cached MCQ matches, generate dynamically via Gemini
        Returns: {'question': str, 'options': dict, 'correct_option': str, 
                  'explanation': str, 'difficulty': str, 'mcq_id': int|None, 'is_dynamic': bool}
        """
        difficulty = self.get_difficulty_for_next_attempt(user_id, chunk.id)
        
        # Get already-shown MCQ IDs for this user+chunk to avoid repeats
        shown_mcq_ids = [
            a.mcq_id for a in self.db.query(ChildChunkAttempt).filter(
                ChildChunkAttempt.user_id == user_id,
                ChildChunkAttempt.child_chunk_id == chunk.id,
                ChildChunkAttempt.mcq_id.isnot(None)
            ).all() if a.mcq_id
        ]
        
        # Try cache first
        cached_mcqs = self.db.query(MCQBank).filter(
            MCQBank.chunk_id == chunk.id,
            MCQBank.difficulty == difficulty
        ).all()
        
        available = [m for m in cached_mcqs if m.id not in shown_mcq_ids]
        if not available:
            available = cached_mcqs  # reuse if all shown
        
        if available:
            mcq = random.choice(available)
            return {
                'question': mcq.question,
                'options': mcq.options,
                'correct_option': mcq.correct_option,
                'explanation': mcq.explanation or '',
                'difficulty': difficulty,
                'mcq_id': mcq.id,
                'is_dynamic': False,
            }
        
        # Fall back to dynamic generation
        return self._generate_dynamic_question(chunk, difficulty)

    def _generate_dynamic_question(self, chunk: Chunk, difficulty: str) -> dict:
        """
        Generate a dynamic MCQ via Gemini based on difficulty.
        Returns same dict structure as get_next_question.
        """
        from app.core.config import settings
        import time
        
        if not settings.gemini_api_key or settings.gemini_api_key in ('change-me', 'replace-me'):
            return self._fallback_question(chunk)
        
        difficulty_instructions = {
            'easy': 'Ask a simple recall or definition question. The answer should be directly stated in the text.',
            'medium': 'Ask an application question. The trainee must understand the concept to answer correctly.',
            'hard': 'Ask an analytical or scenario-based question. The trainee must reason about the content.',
        }
        
        prompt = f"""You are a subject matter expert. Generate exactly 1 multiple-choice question based strictly on the content below.

Difficulty: {difficulty.upper()} — {difficulty_instructions[difficulty]}

CONTENT:
{chunk.content[:3000]}

IMPORTANT: Base your question ONLY on the content above. Do not introduce external domain context.

Return ONLY valid JSON (no markdown, no backticks, no extra text):
{{
  "question": "<question text>",
  "options": {{"A": "<option A>", "B": "<option B>", "C": "<option C>", "D": "<option D>"}},
  "correct_option": "<A, B, C, or D>",
  "explanation": "<why the correct answer is right, explained simply>"
}}"""
        
        try:
            from google import genai as genai_sdk
            client = genai_sdk.Client(api_key=settings.gemini_api_key)
            t0 = time.monotonic()
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            text = response.text.strip()
            if text.startswith('```'):
                lines = text.split('\n')
                text = '\n'.join(lines[1:-1])
            data = json.loads(text)
            return {
                'question': data['question'],
                'options': data['options'],
                'correct_option': data['correct_option'],
                'explanation': data.get('explanation', ''),
                'difficulty': difficulty,
                'mcq_id': None,
                'is_dynamic': True,
            }
        except Exception as e:
            logger.error(f'Dynamic MCQ generation failed: {e}')
            return self._fallback_question(chunk)

    def _fallback_question(self, chunk: Chunk) -> dict:
        """Simple fallback when Gemini is unavailable"""
        return {
            'question': f'What is the main topic discussed in this section?',
            'options': {'A': 'Safety procedures', 'B': 'Quality control', 'C': 'Documentation requirements', 'D': 'The content of this section'},
            'correct_option': 'D',
            'explanation': 'Please review the section content carefully.',
            'difficulty': 'easy',
            'mcq_id': None,
            'is_dynamic': True,
        }

    def record_attempt(
        self,
        user_id: int,
        chunk: Chunk,
        question_data: dict,
        selected_option: str,
        time_taken_seconds: int = 0,
        re_explanation: str | None = None,
    ) -> ChildChunkAttempt:
        """Persist the attempt to DB and return it"""
        attempt_number = self.get_child_attempt_count(user_id, chunk.id) + 1
        is_correct = selected_option.upper() == question_data['correct_option'].upper()
        
        attempt = ChildChunkAttempt(
            user_id=user_id,
            child_chunk_id=chunk.id,
            parent_chunk_id=chunk.parent_chunk_id,
            document_id=chunk.document_id,
            attempt_number=attempt_number,
            difficulty_shown=question_data['difficulty'],
            question_text=question_data['question'],
            options_data=question_data['options'],
            selected_option=selected_option.upper(),
            correct_option=question_data['correct_option'],
            is_correct=is_correct,
            explanation_shown=question_data.get('explanation'),
            re_explanation_shown=re_explanation,
            time_taken_seconds=time_taken_seconds,
            mcq_id=question_data.get('mcq_id'),
        )
        self.db.add(attempt)
        self.db.commit()
        self.db.refresh(attempt)
        
        # After recording, update parent chunk progress
        if chunk.parent_chunk_id:
            self._update_parent_progress(user_id, chunk)
        
        return attempt

    def _update_parent_progress(self, user_id: int, completed_child: Chunk):
        """Recompute and save ParentChunkProgress for this user+parent"""
        parent_id = completed_child.parent_chunk_id
        from sqlalchemy.orm import defer

        # 1. Get all children of this parent (deferring the heavy embedding column)
        all_children = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
            Chunk.parent_chunk_id == parent_id
        ).all()
        
        children_total = len(all_children)
        all_child_ids = [c.id for c in all_children]

        # 2. Bulk query all attempts for all children of this parent
        attempts = self.db.query(ChildChunkAttempt).filter(
            ChildChunkAttempt.user_id == user_id,
            ChildChunkAttempt.child_chunk_id.in_(all_child_ids)
        ).all()

        attempts_by_child = {}
        for a in attempts:
            attempts_by_child.setdefault(a.child_chunk_id, []).append(a)

        # 3. Calculate metrics in memory (avoids N*3 loop DB queries)
        children_passed = 0
        scores = []
        for c in all_children:
            c_attempts = attempts_by_child.get(c.id, [])
            attempt_count = len(c_attempts)
            if attempt_count > 0:
                correct = sum(1 for a in c_attempts if a.is_correct)
                score = round((correct / attempt_count) * 100, 2)
                if score >= PASS_THRESHOLD:
                    children_passed += 1
                scores.append(score)

        avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0
        is_completed = children_passed == children_total and children_total > 0
        
        progress = self.db.query(ParentChunkProgress).filter(
            ParentChunkProgress.user_id == user_id,
            ParentChunkProgress.parent_chunk_id == parent_id
        ).first()
        
        if not progress:
            progress = ParentChunkProgress(
                user_id=user_id,
                parent_chunk_id=parent_id,
                document_id=completed_child.document_id,
                children_total=children_total,
                children_completed=children_passed,
                knowledge_score=avg_score,
                is_completed=is_completed,
                completed_at=datetime.now(timezone.utc) if is_completed else None,
            )
            self.db.add(progress)
        else:
            progress.children_total = children_total
            progress.children_completed = children_passed
            progress.knowledge_score = avg_score
            progress.is_completed = is_completed
            if is_completed and not progress.completed_at:
                progress.completed_at = datetime.now(timezone.utc)
        
        self.db.commit()

    def get_re_explanation(self, chunk: Chunk, wrong_option: str, correct_option: str) -> str:
        """Generate a simpler re-explanation when user answers wrong"""
        from app.core.config import settings
        if not settings.gemini_api_key or settings.gemini_api_key in ('change-me', 'replace-me'):
            return f'The correct answer is {correct_option}. Please re-read the section carefully.'
        
        prompt = f"""A learner answered a question incorrectly.

They chose option {wrong_option} but the correct answer is {correct_option}.

Here is the content they need to understand:
{chunk.content[:2000]}

Write a SHORT, SIMPLE explanation (3-4 sentences max) that helps them understand the correct concept. Use plain, clear language. Focus strictly on what is in the content above."""
        
        try:
            from google import genai as genai_sdk
            client = genai_sdk.Client(api_key=settings.gemini_api_key)
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f'Re-explanation generation failed: {{e}}')
            return f'The correct answer is {correct_option}. Please re-read the section carefully.'
