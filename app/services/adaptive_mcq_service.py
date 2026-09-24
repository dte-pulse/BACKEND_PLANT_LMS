"""
Adaptive MCQ Service
====================
Handles the full adaptive question-answer loop for child chunks:
- Serves cached MCQ bank questions per parent section (medium difficulty)
- Falls back to dynamic Gemini generation when the bank is exhausted
- Tracks attempt history to compute parent section knowledge score
- A parent section passes only after a mastery criterion is met
  (latest attempt correct AND ≥80% correct over the last MASTERY_WINDOW attempts)
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

# A-1: mastery window + ratio for section pass/fail. A single correct answer
# no longer passes a whole section, and one wrong answer no longer wipes it out.
# With a 5-attempt window and 0.8 ratio, a user must get ≥4 of their last 5
# attempts correct (with the latest correct) to pass.
MASTERY_WINDOW = 5  # consider the last N attempts (chronological)
MASTERY_RATIO = 0.8  # ≥80% of the window must be correct

class AdaptiveMcqService:
    def __init__(self, db: Session):
        self.db = db

    def get_child_attempt_count(self, user_id: int, child_chunk_id: int) -> int:
        """How many attempts has this user made on this child chunk?"""
        return self.db.query(ChildChunkAttempt).filter(
            ChildChunkAttempt.user_id == user_id,
            ChildChunkAttempt.child_chunk_id == child_chunk_id
        ).count()

    def is_parent_passed(self, user_id: int, parent_chunk_id: int) -> bool:
        """Returns True when the user has demonstrated mastery of this section.

        Mastery = the latest attempt is correct AND ≥80% of the last
        MASTERY_WINDOW chronological attempts are correct. Ordering uses the
        attempt PK (id) — attempt_number is per-child and NOT chronological
        across children of the same parent (A-1 fix).
        """
        recent = self.db.query(ChildChunkAttempt).filter(
            ChildChunkAttempt.user_id == user_id,
            ChildChunkAttempt.parent_chunk_id == parent_chunk_id
        ).order_by(ChildChunkAttempt.id.desc()).limit(MASTERY_WINDOW).all()

        # A single correct answer must NOT pass a whole section (A-1).
        if len(recent) < 2:
            return False
        if not recent[0].is_correct:  # latest attempt must be correct
            return False
        correct_count = sum(1 for a in recent if a.is_correct)
        return (correct_count / len(recent)) >= MASTERY_RATIO

    def get_child_knowledge_score(self, user_id: int, child_chunk_id: int) -> float:
        """Returns 100.0 if the parent chunk is passed, else 0.0"""
        chunk = self.db.query(Chunk).filter(Chunk.id == child_chunk_id).first()
        if not chunk or not chunk.parent_chunk_id:
            return 0.0
        return 100.0 if self.is_parent_passed(user_id, chunk.parent_chunk_id) else 0.0

    def is_child_passed(self, user_id: int, child_chunk_id: int) -> bool:
        """Returns True if user has passed this child's parent section"""
        return self.get_child_knowledge_score(user_id, child_chunk_id) >= PASS_THRESHOLD

    def get_next_question(self, user_id: int, chunk: Chunk) -> dict:
        """
        Get the next question for this user on this parent chunk section.
        1. Query all sibling chunks under the parent chunk.
        2. Fetch cached MCQs associated with any of those sibling chunks.
        3. Exclude already-shown MCQ IDs for this user in this section.
        4. If no cached MCQ matches, generate dynamically via Gemini using the section content.
        """
        difficulty = 'medium'
        parent_id = chunk.parent_chunk_id
        
        # Get sibling chunks of this section
        sibling_chunks = self.db.query(Chunk).filter(Chunk.parent_chunk_id == parent_id).all()
        sibling_ids = [c.id for c in sibling_chunks]
        
        # Get already-shown MCQ IDs for this user+parent to avoid repeats
        shown_mcq_ids = [
            a.mcq_id for a in self.db.query(ChildChunkAttempt).filter(
                ChildChunkAttempt.user_id == user_id,
                ChildChunkAttempt.parent_chunk_id == parent_id,
                ChildChunkAttempt.mcq_id.isnot(None)
            ).all() if a.mcq_id
        ]
        
        # Try cache first - query all MCQs under sibling chunks
        cached_mcqs = self.db.query(MCQBank).filter(
            MCQBank.chunk_id.in_(sibling_ids),
            MCQBank.difficulty == 'medium'
        ).all()
        
        # Fallback to any cached MCQs if no medium ones exist
        if not cached_mcqs:
            cached_mcqs = self.db.query(MCQBank).filter(
                MCQBank.chunk_id.in_(sibling_ids)
            ).all()
            
        available = [m for m in cached_mcqs if m.id not in shown_mcq_ids]
        # No reuse fallback: when every bank MCQ has been shown, fall through to
        # dynamic Gemini generation below instead of looping the same question.
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

        # No unseen bank MCQ. With an LLM we generate a fresh dynamic question
        # below. Without one, widen to any UNSEEN MCQ across the section (any
        # difficulty), then reuse a seen one as last resort — never loop the
        # same generic fallback question on every attempt.
        from app.clients.llm_client import LLMClient
        if not LLMClient().model:
            all_cached = self.db.query(MCQBank).filter(
                MCQBank.chunk_id.in_(sibling_ids)
            ).all()
            unseen = [m for m in all_cached if m.id not in shown_mcq_ids]
            pool = unseen or all_cached
            if pool:
                mcq = random.choice(pool)
                return {
                    'question': mcq.question,
                    'options': mcq.options,
                    'correct_option': mcq.correct_option,
                    'explanation': mcq.explanation or '',
                    'difficulty': mcq.difficulty,
                    'mcq_id': mcq.id,
                    'is_dynamic': False,
                }

        # Fall back to dynamic generation using parent chunk content
        parent_chunk = self.db.query(ParentChunk).filter(ParentChunk.id == parent_id).first()
        return self._generate_dynamic_question(parent_chunk or chunk, difficulty)

    def _generate_dynamic_question(self, chunk, difficulty: str) -> dict:
        """
        Generate a dynamic MCQ via Gemini based on difficulty.
        Returns same dict structure as get_next_question.
        """
        from app.core.config import settings
        from app.utils.json_parser import parse_json_robustly
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
            from google.genai import types
            client = genai_sdk.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=30_000),
            )
            t0 = time.monotonic()
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
            text = response.text.strip()
            data = parse_json_robustly(text)
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

    def _fallback_question(self, chunk) -> dict:
        """Simple fallback when Gemini is unavailable"""
        return {
            'question': f'What is the main topic discussed in this section?',
            'options': {'A': 'Safety procedures', 'B': 'Quality control', 'C': 'Documentation requirements', 'D': 'The content of this section'},
            'correct_option': 'D',
            'explanation': 'Please review the section content carefully.',
            'difficulty': 'medium',
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
        
        # 2. Check if the parent is passed
        is_passed = self.is_parent_passed(user_id, parent_id)
        
        avg_score = 100.0 if is_passed else 0.0
        children_passed = children_total if is_passed else 0
        is_completed = is_passed
        
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
            elif not is_completed:
                progress.completed_at = None
        
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
            from google.genai import types
            client = genai_sdk.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=30_000),
            )
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            return response.text.strip()
        except Exception as e:
            logger.error(f'Re-explanation generation failed: {{e}}')
            return f'The correct answer is {correct_option}. Please re-read the section carefully.'
