import json
import logging
import time
from google import genai
from google.genai import types
from app.core.config import settings

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self):
        if settings.gemini_api_key and settings.gemini_api_key not in ["change-me", "replace-me"]:
            self._client = genai.Client(api_key=settings.gemini_api_key)
            self.model = 'gemini-2.5-flash'
        else:
            self._client = None
            self.model = None
            logger.warning("Gemini API key is missing. LLMClient will use mock generation.")

    def _log_tokens(self, user_id: int, operation: str, prompt: str, response: str, latency_ms: int = 0):
        try:
            from app.tasks.report_tasks import log_token_usage
            prompt_tokens = max(1, len(prompt) // 4)
            completion_tokens = max(1, len(response) // 4)
            cost = (prompt_tokens * 0.075 + completion_tokens * 0.30) / 1_000_000
            log_token_usage.delay(user_id, operation, prompt_tokens, completion_tokens, cost, False, latency_ms)
        except Exception as e:
            logger.warning(f"Token logging failed: {e}")

    def generate_learning_card(self, content: str, user_id: int = 0) -> str:
        if not self.model:
            return f'Learning card: {content[:280].strip()}'
        
        prompt = f"Generate a very short, engaging 1-sentence learning card summary for the following text:\n\n{content[:2000]}"
        t0 = time.monotonic()
        try:
            response = self._client.models.generate_content(model=self.model, contents=prompt)
            text = response.text.strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._log_tokens(user_id, 'learning_card', prompt, text, latency_ms)
            return text
        except Exception as e:
            logger.error(f"Failed to generate learning card: {e}")
            return f'Learning card: {content[:280].strip()}'

    def generate_mind_map_structure(self, outline_text: str, document_title: str = '', user_id: int = 0) -> list[dict]:
        """Generates a NotebookLM-style 3-tier hierarchical JSON concept tree for a document."""
        if not self.model:
            return []

        prompt = f"""You are an expert curriculum designer. Analyze the following document text outline for '{document_title}' and generate a clean, 3-tier hierarchical concept tree like Google NotebookLM.

Requirements:
1. Level 1: 4 to 6 main high-level themes/chapters.
2. Level 2: 2 to 4 sub-topics per chapter.
3. Level 3: 2 to 4 key concept pills per sub-topic.
4. Each node title MUST be a clean, concise 2 to 5 word concept (e.g., 'Core Design Goals', 'Storage and Retrieval', 'LSM-Trees', 'Fault Tolerance', 'Consistency Models').
5. Absolutely NO raw sentences, NO page numbers (e.g., |161|), NO HTML tags (e.g., <u>p), and NO quotes.

Return ONLY a valid JSON object matching this schema:
{{
  "chapters": [
    {{
      "title": "Chapter or Core Module Name",
      "children": [
        {{
          "title": "Sub-Topic Name",
          "children": [
            {{ "title": "Key Concept Item 1" }},
            {{ "title": "Key Concept Item 2" }}
          ]
        }}
      ]
    }}
  ]
}}

Document Outline Text:
{outline_text[:12000]}
"""

        t0 = time.monotonic()
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            raw = response.text.strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._log_tokens(user_id, 'mind_map_gen', prompt, raw, latency_ms)

            parsed = json.loads(raw)
            if isinstance(parsed, dict) and 'chapters' in parsed:
                return parsed['chapters']
            elif isinstance(parsed, list):
                return parsed
            return []
        except Exception as e:
            logger.error(f"Failed to generate LLM mind map structure: {e}")
            return []


    def generate_mcqs(self, content: str, user_id: int = 0) -> list[dict]:
        fallback_mcqs = [
            {
                'question': f"Which of the following is correct regarding: {content[:100].strip()}?",
                'options': {
                    'A': 'Standard guidelines must be followed as described.',
                    'B': 'Procedures can be ignored.',
                    'C': 'Informal methods are preferred.',
                    'D': 'None of the above.',
                },
                'correct_option': 'A',
                'explanation': 'The document content specifies correct procedures that must be followed.',
                'difficulty': 'medium',
                'type': 'objective',
            }
        ]
        if not self.model:
            return fallback_mcqs

        prompt = f"""
        Generate 2 multiple-choice questions (1 objective, 1 truefalse) based on this text.
        Return strictly a JSON array of objects with keys: 'question', 'options' (object with A,B,C,D for objective, A,B for truefalse), 'correct_option' (A,B,C,D), 'explanation', 'difficulty' (easy/medium/hard), and 'type' (objective/truefalse).
        Do not include markdown backticks or any other text.
        Text: {content[:2000]}
        """
        t0 = time.monotonic()
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            text = response.text.strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._log_tokens(user_id, 'mcq_gen_ingest', prompt, text, latency_ms)
            
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            parsed = json.loads(text.strip())
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed
            return fallback_mcqs
        except Exception as e:
            logger.error(f"Failed to generate mcqs: {e}. Using fallback MCQs.")
            return fallback_mcqs

    def generate_topic_summary(self, chunk_texts: list[str], user_id: int = 0) -> str:
        if not chunk_texts:
            return ''
        
        combined = ' '.join(chunk_texts)[:4000]
        if not self.model:
            return combined[:500]
            
        prompt = f"Provide a brief, concise summary of the following text:\n\n{combined}"
        t0 = time.monotonic()
        try:
            response = self._client.models.generate_content(model=self.model, contents=prompt)
            text = response.text.strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._log_tokens(user_id, 'topic_summary', prompt, text, latency_ms)
            return text
        except Exception as e:
            logger.error(f"Failed to generate topic summary: {e}")
            return combined[:500]

