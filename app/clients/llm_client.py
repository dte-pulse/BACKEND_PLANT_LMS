import json
import logging
import re
import time
from google import genai
from google.genai import types
from app.clients.langfuse_client import MAX_INPUT_CHARS, MAX_OUTPUT_CHARS
from app.core.config import settings

logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self):
        if settings.gemini_api_key and settings.gemini_api_key not in ["change-me", "replace-me"]:
            # Bound every Gemini call (30s). Without this the SDK's default
            # ~10min timeout lets a slow/hung LLM call stall the whole request
            # — the browser then aborts (pending → canceled) and the user never
            # gets an answer. Agents degrade to deterministic fallbacks on
            # timeout, so this is safe to enforce.
            self._client = genai.Client(
                api_key=settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=30_000),
            )
            self.model = 'gemini-2.5-flash'
        else:
            self._client = None
            self.model = None
            logger.warning("Gemini API key is missing. LLMClient will use mock generation.")

    # ── Langfuse generation observation (no-op when observability disabled) ─

    def _llm_observation(self, name: str, user_id: int, operation: str, prompt: str,
                         extra_metadata: dict | None = None):
        """Return a Langfuse ``generation`` observation context manager.

        The SDK records latency automatically; usage (input/output tokens) is
        fed back via ``gen.update(usage_details=...)`` so Langfuse computes cost
        from its model pricing table.
        """
        from app.clients.langfuse_client import langfuse_observation
        metadata = {'operation': operation}
        if extra_metadata:
            metadata.update(extra_metadata)
        return langfuse_observation(
            name=name,
            as_type='generation',
            user_id=user_id or None,
            tags=['llm', operation],
            metadata=metadata,
            model=self.model,
            input_data={'prompt': prompt[:MAX_INPUT_CHARS]},
        )

    @staticmethod
    def _usage_details(response, prompt: str, text: str) -> dict:
        """Langfuse usage from Gemini's REAL usage_metadata (estimate fallback).

        Delegates to ``tokenizer.langfuse_usage_details`` — the single source of
        truth for the canonical ``input`` / ``output`` / ``input_cached_tokens``
        keys matching the predefined ``gemini-2.5-flash`` price definition
        verbatim ($0.30/1M in, $2.50/1M out, $0.03/1M cached).
        """
        from app.utils.tokenizer import langfuse_usage_details
        return langfuse_usage_details(response, prompt, text)

    def generate_json(self, prompt: str, user_id: int = 0, operation: str = 'llm_json') -> dict | list | None:
        """Call Gemini with response_mime_type=application/json and parse robustly.

        Used by the adaptive learning agents for structured outputs (questions,
        evaluations, study plans). Returns None on any failure so agents can
        fall back to deterministic behavior.
        """
        if not self.model:
            return None
        t0 = time.monotonic()
        try:
            with self._llm_observation(f'{operation}-json', user_id, operation, prompt) as gen:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type='application/json'),
                )
                text = response.text.strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = self._usage_details(response, prompt, text)
                self._log_tokens(user_id, operation, prompt, text, latency_ms, usage)
                gen.update(output=text[:MAX_OUTPUT_CHARS], usage_details=usage)
            # Parse INSIDE the try: a malformed/truncated response must degrade
            # to None (the documented contract — agents fall back to
            # deterministic behavior), NOT raise. Before this fix an
            # unparseable reply 500'd the EvaluatorAgent's wrong-answer
            # diagnosis and the adaptive answer submit endpoint.
            from app.utils.json_parser import parse_json_robustly
            return parse_json_robustly(text)
        except Exception as e:
            logger.error(f"Failed to generate JSON via LLM ({operation}): {e}")
            return None

    def generate_text(self, prompt: str, user_id: int = 0, operation: str = 'llm_text') -> str | None:
        """Call Gemini for free-text output with token logging. Returns None on
        any failure so agents can fall back to deterministic behavior."""
        if not self.model:
            return None
        t0 = time.monotonic()
        try:
            with self._llm_observation(f'{operation}-text', user_id, operation, prompt) as gen:
                response = self._client.models.generate_content(model=self.model, contents=prompt)
                text = response.text.strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = self._usage_details(response, prompt, text)
                self._log_tokens(user_id, operation, prompt, text, latency_ms, usage)
                gen.update(output=text[:MAX_OUTPUT_CHARS], usage_details=usage)
        except Exception as e:
            logger.error(f"Failed to generate text via LLM ({operation}): {e}")
            return None
        return text





    def _log_tokens(self, user_id: int, operation: str, prompt: str, response: str, latency_ms: int = 0,
                    usage: dict | None = None):
        """Record token usage to the internal ledger. Prefers REAL Gemini usage
        (``usage`` dict from ``_usage_details``); falls back to estimates so the
        mock/fallback paths still log."""
        try:
            from app.tasks.report_tasks import log_token_usage
            from app.utils.tokenizer import estimate_tokens, estimate_cost
            if usage and (usage.get('input') or usage.get('output')):
                prompt_tokens = (usage.get('input') or 0) + (usage.get('input_cached_tokens') or 0)
                completion_tokens = usage.get('output') or 0
            else:
                prompt_tokens = estimate_tokens(prompt)
                completion_tokens = estimate_tokens(response)
            cost = estimate_cost(prompt_tokens, completion_tokens)
            log_token_usage.delay(user_id, operation, prompt_tokens, completion_tokens, cost, False, latency_ms)
        except Exception as e:
            logger.warning(f"Token logging failed: {e}")

    def generate_contextual_header(
        self,
        chunk_content: str,
        section_title: str,
        document_title: str,
        section_index: int,
        total_sections: int,
        user_id: int = 0,
    ) -> str:
        """Upgrade 2 — Contextual Retrieval (Anthropic): write a 1-2 sentence
        situating header for a child chunk.

        The header answers "where in the document is this, and what is it
        about?" so the chunk embeds/retrieves well in isolation. Called at
        ingest; the result is stored on chunks.contextual_header and prepended
        at embed / BM25 / answer time. Returns '' on any failure — ingestion
        must never fail because a context header could not be written (the
        pipeline then runs exactly as before, without context)."""
        if not self.model:
            return ''

        prompt = f"""You are indexing a technical training document for a retrieval system.\nHere is the position of the chunk within the document:\n\n<document>\n{document_title}\n</document>\n\n<section>\nSection {section_index} of {total_sections}: {section_title}\n</section>\n\n<chunk>\n{chunk_content[:1500]}\n</chunk>\n\nWrite 1-2 short sentences that situate this chunk within the overall document for someone who sees ONLY this chunk. Start with "This chunk is from...". Give context that helps retrieval: what part of the document it belongs to, and what specific topic it covers. Use only information present in the document/section/chunk above. Do NOT summarize the chunk's full content — situate it.\n\nAnswer with ONLY the context sentences, nothing else."""

        try:
            text = self.generate_text(prompt, user_id=user_id, operation='contextual_header')
            if not text:
                return ''
            # Basic hygiene: single line, trimmed, hard cap (the header is
            # prepended to chunk text — it must stay small).
            text = ' '.join(text.split()).strip()
            if len(text) > 400:
                text = text[:397].rstrip() + '...'
            # Refuse obvious non-answers (refusals / meta commentary).
            lowered = text.lower()
            if lowered.startswith(("i'm sorry", 'i cannot', 'as an ai')):
                return ''
            return text
        except Exception as e:
            logger.warning(f'Contextual header generation failed (non-fatal): {e}')
            return ''

    def generate_table_caption(
        self,
        serialized_rows: str,
        document_title: str,
        section_title: str,
        user_id: int = 0,
    ) -> str:
        """P2 #3 — one-line description of what a table *is about*, so a chunk
        of pipe-separated cells embeds like prose ("Hold time acceptance
        criteria for ..." instead of "Hold Time | Acceptance | ...".

        Called at ingest for table chunks only; stored on the chunk's
        contextual_header. Returns '' on any failure — captioning is an
        optimization, never a correctness requirement."""
        if not self.model:
            return ''

        prompt = f"""You are indexing a technical document for a retrieval system.\n<document>\n{document_title}\n</document>\n\n<section>\n{section_title}\n</section>\n\n<table>\n{serialized_rows[:2000]}\n</table>\n\nWrite ONE sentence (max 25 words) stating what this table contains, in the form "Table of X ...". Include the key column names so someone searching for those terms finds it. Use only information from the table above.\n\nAnswer with ONLY the sentence, nothing else."""

        try:
            text = self.generate_text(prompt, user_id=user_id, operation='table_caption')
            if not text:
                return ''
            text = ' '.join(text.split()).strip()
            if len(text) > 250:
                text = text[:247].rstrip() + '...'
            lowered = text.lower()
            if lowered.startswith(("i'm sorry", 'i cannot', 'as an ai')):
                return ''
            return text
        except Exception as e:
            logger.warning(f'Table caption generation failed (non-fatal): {e}')
            return ''

    def generate_learning_card(self, content: str, user_id: int = 0) -> str:
        from app.utils.text_utils import strip_preceding_context
        content = strip_preceding_context(content)  # C-1: never learn from the recap
        if not self.model:
            return f'Learning card: {content[:280].strip()}'
        
        prompt = f"Generate a very short, engaging 1-sentence learning card summary for the following text:\n\n{content[:2000]}"
        t0 = time.monotonic()
        try:
            with self._llm_observation('learning-card', user_id, 'learning_card', prompt) as gen:
                response = self._client.models.generate_content(model=self.model, contents=prompt)
                text = response.text.strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = self._usage_details(response, prompt, text)
                self._log_tokens(user_id, 'learning_card', prompt, text, latency_ms, usage)
                gen.update(output=text[:MAX_OUTPUT_CHARS], usage_details=usage)
        except Exception as e:
            logger.error(f"Failed to generate learning card: {e}")
            return f'Learning card: {content[:280].strip()}'
        return text





    def generate_mind_map_structure(self, outline_text: str, document_title: str = '', user_id: int = 0) -> list[dict]:
        """Generates a NotebookLM-style 3-tier hierarchical JSON concept tree for a document."""
        if not self.model:
            return []

        prompt = f"""You are an expert curriculum designer and knowledge architect. Your task is to analyze the document outline below for '{document_title}' and produce a clean, hierarchical concept tree like Google NotebookLM.

The outline lists the document's sections as "Section 0:", "Section 1:", ... Each section number is a PERMANENT INDEX into the document's section list.

CRITICAL RULES (must follow all of them):
1. Extract REAL DOMAIN KNOWLEDGE — topics, techniques, processes, systems, principles — from the actual document content.
2. COMPLETELY IGNORE any lines starting with "[Preceding Section:", "...", or any meta-prefixes. These are system artifacts, not content.
3. Level 1 (chapters): 4–6 high-level thematic modules. Each chapter MUST carry a "section_indexes" array listing the EXACT section numbers (0-based integers, copied from the "Section N:" labels) that belong to that chapter.
4. COVERAGE: every section number appearing in the outline MUST be listed in exactly one chapter's "section_indexes". Do not skip sections, do not repeat a section across chapters, do not invent section numbers.
5. Level 2 (sub-topics): 2–4 meaningful sub-topics per chapter reflecting distinct concepts across its sections (these are descriptive — the "section_indexes" binding, not the sub-topics, defines what belongs to a chapter).
6. Level 3 (concept pills): 2–4 concrete key terms or concept phrases per sub-topic.
7. Every title MUST be a clean 2–5 word concept phrase (e.g., "Data Replication Strategies", "LSM-Tree Compaction", "Fault Tolerance Design").
8. NO raw sentences. NO page numbers (e.g., |161|). NO HTML tags. NO quotes around titles. NO generic labels like "Overview" or "Introduction" used alone.

Return ONLY a valid JSON object — no markdown, no explanation text — matching this exact schema:
{{
  "chapters": [
    {{
      "title": "Thematic Chapter Name",
      "section_indexes": [0, 2, 5],
      "children": [
        {{
          "title": "Sub-Topic Name",
          "children": [
            {{ "title": "Key Concept Phrase 1" }},
            {{ "title": "Key Concept Phrase 2" }}
          ]
        }}
      ]
    }}
  ]
}}

Document Outline Text:
{outline_text[:14000]}
"""

        t0 = time.monotonic()
        try:
            with self._llm_observation('mind-map-structure', user_id, 'mind_map_gen', prompt,
                                       extra_metadata={'document_title': document_title[:200]}) as gen:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config={'response_mime_type': 'application/json'}
                )
                raw = response.text.strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = self._usage_details(response, prompt, raw)
                self._log_tokens(user_id, 'mind_map_gen', prompt, raw, latency_ms, usage)
                gen.update(output=raw[:MAX_OUTPUT_CHARS], usage_details=usage)

            from app.utils.json_parser import parse_json_robustly
            parsed = parse_json_robustly(raw)
            if isinstance(parsed, dict) and 'chapters' in parsed:
                return parsed['chapters']
            elif isinstance(parsed, list):
                return parsed
            return []
        except Exception as e:
            logger.error(f"Failed to generate LLM mind map structure: {e}")
            return []


    def generate_mcqs(self, content: str, user_id: int = 0) -> list[dict]:
        from app.utils.text_utils import strip_preceding_context
        content = strip_preceding_context(content)  # C-1: never generate from the recap
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
            with self._llm_observation('generate-mcqs', user_id, 'mcq_gen_ingest', prompt) as gen:
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                    ),
                )
                text = response.text.strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = self._usage_details(response, prompt, text)
                self._log_tokens(user_id, 'mcq_gen_ingest', prompt, text, latency_ms, usage)
                gen.update(output=text[:MAX_OUTPUT_CHARS], usage_details=usage)

            from app.utils.json_parser import parse_json_robustly
            parsed = parse_json_robustly(text)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed
            return fallback_mcqs
        except Exception as e:
            logger.error(f"Failed to generate mcqs: {e}. Using fallback MCQs.")
            return fallback_mcqs

    def generate_topic_summary(self, chunk_texts: list[str], user_id: int = 0) -> str:
        if not chunk_texts:
            return ''

        combined = '\n\n'.join(chunk_texts)
        if not self.model:
            return combined[:500]

        # Representative sampling: beginning + middle + end so later sections
        # are not silently excluded when the document is large.  (G-15 fix)
        max_chars = 12_000
        if len(combined) > max_chars:
            portion = max_chars // 3
            mid = len(combined) // 2
            combined = (
                combined[:portion]
                + '\n\n[...middle section...]\n\n'
                + combined[mid - portion // 2: mid + portion // 2]
                + '\n\n[...later section...]\n\n'
                + combined[-portion:]
            )

        prompt = f"Provide a brief, concise summary of the following text:\n\n{combined}"
        t0 = time.monotonic()
        try:
            with self._llm_observation('topic-summary', user_id, 'topic_summary', prompt) as gen:
                response = self._client.models.generate_content(model=self.model, contents=prompt)
                text = response.text.strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = self._usage_details(response, prompt, text)
                self._log_tokens(user_id, 'topic_summary', prompt, text, latency_ms, usage)
                gen.update(output=text[:MAX_OUTPUT_CHARS], usage_details=usage)
        except Exception as e:
            logger.error(f"Failed to generate topic summary: {e}")
            return combined[:500]
        return text





    def compare_document_versions(self, prev_text: str, new_text: str, user_id: int = 0) -> str:
        """Compare the text of two versions of the document and summarize the key differences/revision changelog."""
        if not self.model:
            return "No previous version available or mock comparison."
            
        # I-8: compare representative beginning/middle/end samples of each version
        # instead of only the first 6000 chars, so later-section changes are found.
        from app.utils.text_utils import strip_preceding_context, representative_sample
        prev_sample = representative_sample(strip_preceding_context(prev_text), 6000)
        new_sample = representative_sample(strip_preceding_context(new_text), 6000)

        prompt = f"""You are a pharmaceutical document control assistant.
Compare the previous version and the new version of this SOP document.
Highlight the key changes, including:
1. Significant additions (new rules, requirements, instructions).
2. Significant deletions or omissions.
3. Key procedural updates or changes.

Be highly professional, clear, and output the summary in clean bullet points.

---
PREVIOUS VERSION TEXT:
{prev_sample}

---
NEW VERSION TEXT:
{new_sample}
"""
        t0 = time.monotonic()
        try:
            with self._llm_observation('compare-document-versions', user_id, 'compare_versions', prompt) as gen:
                response = self._client.models.generate_content(model=self.model, contents=prompt)
                text = response.text.strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = self._usage_details(response, prompt, text)
                self._log_tokens(user_id, 'compare_versions', prompt, text, latency_ms, usage)
                gen.update(output=text[:MAX_OUTPUT_CHARS], usage_details=usage)
        except Exception as e:
            logger.error(f"Failed to compare document versions: {e}")
            return "Failed to generate revision comparison changelog."
        return text





    def generate_section_title(self, content: str, user_id: int = 0) -> str:
        """Generate a short, descriptive 2-5 word title for a block of text.
        I-6 fix: token usage is now logged for cost visibility."""
        from app.utils.text_utils import strip_preceding_context
        content = strip_preceding_context(content)  # C-1
        if not self.model:
            return "Section"
        prompt = f"Analyze the following text block and generate a short, clean, descriptive 2-5 word section title. Return ONLY the title itself, no punctuation or extra text.\n\nText:\n{content[:2000]}"
        t0 = time.monotonic()
        try:
            with self._llm_observation('section-title', user_id, 'section_title', prompt) as gen:
                response = self._client.models.generate_content(model=self.model, contents=prompt)
                text = response.text.strip().replace('"', '').replace("'", "")[:120]
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = self._usage_details(response, prompt, text)
                self._log_tokens(user_id, 'section_title', prompt, text, latency_ms, usage)
                gen.update(output=text[:MAX_OUTPUT_CHARS], usage_details=usage)
        except Exception as e:
            logger.error(f"Failed to generate section title: {e}")
            return "Section"
        return text

    def generate_batched_section_titles(
        self,
        excerpts: list[str],
        user_id: int = 0,
    ) -> list[str]:
        """P2 #5 — title ALL unstructured-document sections in ONE LLM call.

        The blind/clustered fallback previously burned one call per ~2000-token
        section (≈50 calls for a long document). This method sends every
        section's excerpt in a single prompt and returns one title per index.

        Robustness contract: the returned list ALWAYS matches ``len(excerpts)``
        — indices the LLM skipped get deterministic first-word fallbacks, so
        callers can zip without shape checks. Returns first-word fallbacks for
        ALL entries on any failure (matches generate_section_title's
        never-fail contract)."""
        if not excerpts:
            return []

        def _fallback(text: str) -> str:
            words = (text or '').split()
            return ' '.join(words[:4]) if words else 'Section'

        fallbacks = [_fallback(e) for e in excerpts]
        if not self.model:
            return fallbacks

        listing = '\n'.join(
            f'{i}: {e[:300]}'.replace('\n', ' ')
            for i, e in enumerate(excerpts)
        )
        prompt = f"""You are indexing a document for a retrieval system. Below are numbered excerpts from consecutive sections of ONE document.\n\n{listing}\n\nFor EACH numbered excerpt, write a short descriptive section title (2-6 words).\n\nAnswer with EXACTLY one line per excerpt, in this exact format and order:\n0: Title for excerpt zero\n1: Title for excerpt one\n...\nNo extra lines, no commentary. Use only information present in the excerpts."""

        t0 = time.monotonic()
        try:
            with self._llm_observation('batched-titles', user_id, 'section_title', prompt) as gen:
                response = self._client.models.generate_content(model=self.model, contents=prompt)
                text = (response.text or '').strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                usage = self._usage_details(response, prompt, text)
                self._log_tokens(user_id, 'section_title', prompt, text, latency_ms, usage)
                gen.update(output=text[:MAX_OUTPUT_CHARS], usage_details=usage)

            titles = list(fallbacks)
            n_seen = 0
            for line in text.splitlines():
                m = re.match(r'^\s*(\d+)\s*[:.\-]\s*(.+)$', line.strip())
                if not m:
                    continue
                idx = int(m.group(1))
                title = m.group(2).strip().replace('"', '').replace("'", '')[:120]
                if 0 <= idx < len(titles) and title:
                    titles[idx] = title
                    n_seen += 1
            if n_seen == 0:
                logger.warning('Batched titles: no parseable lines — using fallbacks')
            return titles
        except Exception as e:
            logger.error(f'Batched section-title generation failed: {e}')
            return fallbacks






