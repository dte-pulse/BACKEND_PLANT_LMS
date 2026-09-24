"""QuestionGeneratorAgent — builds content-grounded questions of the right
difficulty and format for the learner's current state.

Bank-first (reuses the ingestion MCQ bank), dynamic Gemini generation on a
miss. Dynamic questions are validated and persisted back to the bank so the
pool grows over time (A-3 fix) instead of repeating the same 3 questions.
"""
import logging
import random

from app.agents.base_agent import BaseAgent
from app.utils.text_utils import strip_preceding_context

logger = logging.getLogger(__name__)

# Which question formats make sense at each difficulty level.
FORMAT_BY_DIFFICULTY = {
    'easy': ['objective'],                      # recall: 4-option MCQ
    'medium': ['objective', 'true_false'],      # application
    'hard': ['scenario', 'objective'],          # analytical / scenario-based
}

# MCQBank.type value for each format (scenario is stored as an objective row).
TYPE_FOR_FORMAT = {'objective': 'objective', 'true_false': 'true_false', 'scenario': 'objective'}
# Reverse map for reporting a truthful format when widening to any-difficulty MCQs.
FORMAT_FOR_TYPE = {'objective': 'objective', 'true_false': 'true_false'}

DIFFICULTY_SPEC = {
    'easy': 'Ask a simple recall or definition question. The answer must be directly stated in the text.',
    'medium': 'Ask an application question. The learner must understand the concept to answer correctly.',
    'hard': 'Ask an analytical or scenario-based question. The learner must reason about the content.',
}

FORMAT_SPEC = {
    'objective': 'Multiple choice with exactly 4 options labeled A, B, C, D.',
    'true_false': 'True/False question with exactly 2 options: A: "True", B: "False".',
    'scenario': 'A workplace scenario question with exactly 4 plausible options labeled A, B, C, D. The learner must apply the content to the situation.',
}


class QuestionGeneratorAgent(BaseAgent):
    def generate(
        self,
        user_id: int,
        chunk,
        difficulty: str = 'medium',
        prefer_format: str | None = None,
        exclude_mcq_ids: list[int] | None = None,
    ) -> dict:
        """Return a question dict (same shape as the legacy MCQ response):
        question, options, correct_option, explanation, difficulty, format,
        mcq_id (None when dynamic), is_dynamic.
        """
        exclude_mcq_ids = exclude_mcq_ids or []
        formats = FORMAT_BY_DIFFICULTY.get(difficulty, ['objective'])
        if prefer_format and prefer_format in formats:
            formats = [prefer_format]
        fmt = random.choice(formats)

        from app.models.mcq import MCQBank

        # 1. Bank-first: an unused MCQ matching difficulty (+ format type).
        q_type = TYPE_FOR_FORMAT[fmt]
        bank_mcqs = self.db.query(MCQBank).filter(
            MCQBank.chunk_id == chunk.id,
            MCQBank.difficulty == difficulty,
            MCQBank.type == q_type,
        ).all()
        available = [m for m in bank_mcqs if m.id not in exclude_mcq_ids]
        if available:
            mcq = random.choice(available)
            return {
                'question': mcq.question,
                'options': mcq.options,
                'correct_option': mcq.correct_option,
                'explanation': mcq.explanation or '',
                'difficulty': difficulty,
                'format': fmt,
                'mcq_id': mcq.id,
                'is_dynamic': False,
            }

        # 2. Every MCQ of this difficulty has already been shown. The old code
        # reused them here — which made the SAME question loop until the
        # mastery threshold was hit (confirmed in prod: chunk 446 served mcq
        # 1279 three times in a row). With an LLM available we generate a fresh
        # dynamic question instead (validated + persisted, so the pool grows).
        if not getattr(self.llm(), 'model', None):
            # No LLM: widen to any UNSEEN MCQ for this chunk (any difficulty)
            # so the learner still gets variety rather than a repeat.
            all_bank = self.db.query(MCQBank).filter(MCQBank.chunk_id == chunk.id).all()
            unseen = [m for m in all_bank if m.id not in exclude_mcq_ids]
            pool = unseen or all_bank
            if pool:
                mcq = random.choice(pool)
                return {
                    'question': mcq.question,
                    'options': mcq.options,
                    'correct_option': mcq.correct_option,
                    'explanation': mcq.explanation or '',
                    'difficulty': mcq.difficulty,
                    # Report the MCQ's REAL format, not the planned one — the
                    # widening pool is not type-filtered.
                    'format': FORMAT_FOR_TYPE.get(mcq.type, 'objective'),
                    'mcq_id': mcq.id,
                    'is_dynamic': False,
                }

        # 3. Bank exhausted → dynamic generation (validated + persisted to the bank).
        return self._generate_dynamic(user_id, chunk, difficulty, fmt)

    def _generate_dynamic(self, user_id: int, chunk, difficulty: str, fmt: str) -> dict:
        from app.models.mcq import MCQBank

        content = strip_preceding_context(chunk.content or '')[:3000]
        prompt = f"""You are a subject matter expert and training assessor. Generate exactly 1 question based STRICTLY on the content below.

Format: {FORMAT_SPEC[fmt]}
Difficulty: {DIFFICULTY_SPEC[difficulty]}

CONTENT:
{content}

IMPORTANT: Base the question ONLY on the content above. Do not introduce external domain context. The correct answer must be directly supported by the text.

Return ONLY valid JSON:
{{
  "question": "<question text>",
  "options": {{"A": "<option A>", "B": "<option B>", "C": "<option C>", "D": "<option D>"}},
  "correct_option": "<A, B, C or D>",
  "explanation": "<why the correct answer is right, explained simply>"
}}"""

        data = self.llm().generate_json(prompt, user_id, 'agent_question_gen')
        if not data or not isinstance(data, dict) or not data.get('question'):
            return self._fallback_question(chunk, difficulty, fmt)

        options = data.get('options') or {}
        correct = str(data.get('correct_option', '')).upper()
        if correct not in options or len(options) < 2:
            return self._fallback_question(chunk, difficulty, fmt)

        # Persist the generated question so the bank grows (A-3 fix). A DB
        # failure must not 500 the question — serve it unpinned instead.
        try:
            mcq = MCQBank(
                document_id=chunk.document_id,
                topic_id=chunk.topic_id or 0,
                chunk_id=chunk.id,
                question=data['question'],
                options=options,
                correct_option=correct,
                explanation=data.get('explanation', ''),
                difficulty=difficulty,
                type=TYPE_FOR_FORMAT[fmt],
            )
            self.db.add(mcq)
            self.db.commit()
            self.db.refresh(mcq)
            mcq_id = mcq.id
        except Exception as e:  # noqa: BLE001 — persist is best-effort
            logger.warning(f'Failed to persist generated MCQ for chunk {chunk.id}: {e}')
            self.db.rollback()
            mcq_id = None

        return {
            'question': data['question'],
            'options': options,
            'correct_option': correct,
            'explanation': data.get('explanation', ''),
            'difficulty': difficulty,
            'format': fmt,
            'mcq_id': mcq_id,
            'is_dynamic': True,
        }

    @staticmethod
    def _fallback_question(chunk, difficulty: str, fmt: str) -> dict:
        """Deterministic fallback when the LLM is unavailable."""
        page_no = getattr(chunk, 'page_no', 1)
        if fmt == 'true_false':
            return {
                'question': f'True or False: The section on page {page_no} describes requirements that must be followed.',
                'options': {'A': 'True', 'B': 'False'},
                'correct_option': 'A',
                'explanation': 'Please re-read the section content carefully.',
                'difficulty': difficulty,
                'format': 'true_false',
                'mcq_id': None,
                'is_dynamic': True,
            }
        return {
            'question': f'What is the main topic discussed in this section (page {page_no})?',
            'options': {
                'A': 'Safety procedures',
                'B': 'Quality control',
                'C': 'Documentation requirements',
                'D': 'The content of this section',
            },
            'correct_option': 'D',
            'explanation': 'Please review the section content carefully.',
            'difficulty': difficulty,
            'format': 'objective',
            'mcq_id': None,
            'is_dynamic': True,
        }
