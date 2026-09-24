"""EvaluatorAgent — grades the learner's answer and, on a wrong answer,
produces a targeted re-explanation that diagnoses WHICH part of the concept
they likely misunderstood and exactly what to re-read.

The diagnosis is what feeds the WeaknessAgent's insight and makes the
remediation feel personal rather than "please try again".
"""
import logging

from app.agents.base_agent import BaseAgent
from app.utils.text_utils import strip_preceding_context

logger = logging.getLogger(__name__)


class EvaluatorAgent(BaseAgent):
    def evaluate(
        self,
        user_id: int,
        chunk,
        question_data: dict,
        selected_option: str,
        time_taken_seconds: int = 0,
    ) -> dict:
        correct_option = str(question_data.get('correct_option', '')).upper()
        is_correct = bool(correct_option) and selected_option.upper() == correct_option

        result = {
            'is_correct': is_correct,
            'correct_option': correct_option,
            'explanation': question_data.get('explanation', '') or '',
            're_explanation': None,
            'diagnosis': None,
        }

        if not is_correct:
            result['re_explanation'], result['diagnosis'] = self._diagnose(
                user_id, chunk, question_data, selected_option, correct_option
            )
        return result

    def _diagnose(
        self, user_id: int, chunk, question_data: dict, wrong_option: str, correct_option: str
    ) -> tuple[str, str | None]:
        """LLM: explain the concept simply + name the specific misunderstanding.

        Returns (re_explanation, diagnosis). Falls back to a deterministic
        explanation when the LLM is unavailable.
        """
        fallback_explanation = (
            f'The correct answer is {correct_option}. Please re-read the section '
            f'on page {chunk.page_no} carefully.'
        )

        content = strip_preceding_context(chunk.content or '')[:2000]
        prompt = f"""A learner answered a training question incorrectly.

They chose option {wrong_option} but the correct answer is {correct_option}.
Question: {question_data.get('question', '')}

Here is the content they need to understand:
{content}

Write a SHORT, SIMPLE explanation (3-4 sentences max) in plain language that helps them understand the correct concept, strictly based on the content above.

Return ONLY valid JSON:
{{
  "re_explanation": "<simple explanation of the correct concept>",
  "diagnosis": "<one short sentence naming the specific misconception or missing detail that likely caused the wrong choice, or null>"
}}"""

        data = self.llm().generate_json(prompt, user_id, 'agent_evaluate')
        if data and isinstance(data, dict) and data.get('re_explanation'):
            return str(data['re_explanation']).strip(), (
                str(data['diagnosis']).strip() if data.get('diagnosis') else None
            )
        return fallback_explanation, None
