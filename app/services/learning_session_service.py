"""
Phase 3 — Update LearningSessionService to persist UserProgress after each chunk answer.
"""
import json
import logging
import random
from datetime import datetime, timezone
from sqlalchemy.orm import Session, defer

from app.models.chunk import Chunk
from app.models.parent_chunk import ParentChunk
from app.models.mcq import MCQBank
from app.models.user_progress import UserProgress
from app.services.rag_service import RagService

logger = logging.getLogger(__name__)


class LearningSessionService:
    def __init__(self, db: Session, user_id: int = 0):
        self.db = db
        self.user_id = user_id
        self.rag = RagService(db, user_id=user_id)
        from app.services.adaptive_mcq_service import AdaptiveMcqService
        self.adaptive_mcq = AdaptiveMcqService(db)

    # ── Parent-Child aware methods (Phase 4) ────────────────────────────────

    def get_document_structure(self, document_id: int) -> dict:
        try:
            from app.models.parent_chunk_progress import ParentChunkProgress
        except ImportError:
            ParentChunkProgress = None
        
        # 1. Fetch all parents with deferred embeddings
        parent_chunks = self.db.query(ParentChunk).options(defer(ParentChunk.embedding)).filter(
            ParentChunk.document_id == document_id
        ).order_by(ParentChunk.section_index).all()
        
        has_parent_child = len(parent_chunks) > 0
        parents_data = []
        total_children = 0
        
        if has_parent_child:
            # 2. Fetch all child chunks in one query with deferred embeddings
            children = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(
                Chunk.document_id == document_id
            ).order_by(Chunk.parent_chunk_id, Chunk.child_index).all()
            
            children_by_parent = {}
            for c in children:
                children_by_parent.setdefault(c.parent_chunk_id, []).append(c)
                
            # 3. Fetch all progress records for the user and document in one query
            progress_by_parent = {}
            if ParentChunkProgress:
                progress_records = self.db.query(ParentChunkProgress).filter(
                    ParentChunkProgress.user_id == self.user_id,
                    ParentChunkProgress.document_id == document_id
                ).all()
                progress_by_parent = {p.parent_chunk_id: p for p in progress_records}
                
            # Assemble the parents structure in memory
            for parent in parent_chunks:
                p_children = children_by_parent.get(parent.id, [])
                parents_data.append({
                    'parent': parent,
                    'children': p_children,
                    'progress': progress_by_parent.get(parent.id)
                })
                total_children += len(p_children)
                
        return {
            'parents': parents_data,
            'has_parent_child': has_parent_child,
            'total_children': total_children,
        }

    def get_current_child_for_user(self, user_id: int, document_id: int) -> Chunk | None:
        from app.models.parent_chunk import ParentChunk
        # Find all parents
        parents = self.db.query(ParentChunk).filter(
            ParentChunk.document_id == document_id
        ).order_by(ParentChunk.section_index).all()
        
        for parent in parents:
            children = self.db.query(Chunk).filter(
                Chunk.parent_chunk_id == parent.id
            ).order_by(Chunk.child_index).all()
            
            for child in children:
                if not self.adaptive_mcq.is_child_passed(user_id, child.id):
                    return child
        return None

    def get_next_question_for_child(self, user_id: int, chunk_id: int) -> dict:
        """Agent-driven question (CurriculumAgent → QuestionGeneratorAgent)."""
        chunk = self.db.query(Chunk).filter(Chunk.id == chunk_id).first()
        if not chunk:
            raise ValueError(f'Chunk {chunk_id} not found')
        from app.agents.adaptive_agent_service import AdaptiveAgentService
        return AdaptiveAgentService(self.db, user_id=user_id).get_next_question(user_id, chunk)

    def submit_child_answer(
        self,
        user_id: int,
        chunk_id: int,
        question_data: dict,
        selected_option: str,
        time_taken_seconds: int = 0,
    ) -> dict:
        """Delegates to the AdaptiveAgentService coordinator (evaluator agent,
        weakness agent, curriculum agent) — single source of truth for the
        adaptive answer loop."""
        chunk = self.db.query(Chunk).options(defer(Chunk.embedding)).filter(Chunk.id == chunk_id).first()
        if not chunk:
            raise ValueError(f'Chunk {chunk_id} not found')
        from app.agents.adaptive_agent_service import AdaptiveAgentService
        return AdaptiveAgentService(self.db, user_id=user_id).submit_answer(
            user_id=user_id,
            chunk=chunk,
            question_data=question_data,
            selected_option=selected_option,
            time_taken_seconds=time_taken_seconds,
        )

    def _chunk_to_dict(self, chunk: Chunk) -> dict:
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

    # ── Backward compatibility: keep these methods from original ──────────────

    # ─── Chunks ──────────────────────────────────────────────────────────────

    def get_chunks(self, document_id: int) -> list[Chunk]:
        return (
            self.db.query(Chunk)
            .filter(Chunk.document_id == document_id)
            .order_by(Chunk.chunk_index)
            .all()
        )

    def get_chunk(self, chunk_id: int) -> Chunk | None:
        return self.db.query(Chunk).filter(Chunk.id == chunk_id).first()

    # ─── Progress Persistence ─────────────────────────────────────────────────

    def get_or_create_progress(self, user_id: int, document_id: int, topic_id: int) -> UserProgress:
        """Get existing progress record or create a new one."""
        progress = (
            self.db.query(UserProgress)
            .filter(UserProgress.user_id == user_id, UserProgress.document_id == document_id)
            .first()
        )
        if not progress:
            progress = UserProgress(
                user_id=user_id,
                document_id=document_id,
                topic_id=topic_id or 0,
                current_chunk_id=None,
                current_page=1,
                completion_percentage=0.0,
                time_spent_seconds=0,
            )
            self.db.add(progress)
            self.db.commit()
            self.db.refresh(progress)
        return progress

    def save_progress(
        self,
        user_id: int,
        document_id: int,
        current_chunk: Chunk,
        completed_chunk_ids: list[int],
        total_chunks: int,
        time_spent_seconds: int = 0,
    ) -> UserProgress:
        """Persist learning progress for a user on a document, caching in Redis and syncing asynchronously."""
        from app.core.redis import redis_client
        from app.tasks.report_tasks import sync_progress_to_db
        
        topic_id = current_chunk.topic_id or 0
        redis_key = f"user:{user_id}:progress:{document_id}"
        
        # Get existing accumulated time from Redis first, fallback to DB
        existing_time = redis_client.hget(redis_key, "time_spent_seconds")
        if existing_time is not None:
            total_time = int(existing_time) + time_spent_seconds
        else:
            db_progress = (
                self.db.query(UserProgress)
                .filter(UserProgress.user_id == user_id, UserProgress.document_id == document_id)
                .first()
            )
            total_time = (db_progress.time_spent_seconds if db_progress else 0) + time_spent_seconds

        pct = (len(completed_chunk_ids) / total_chunks * 100) if total_chunks > 0 else 0.0
        pct = round(pct, 2)
        
        # Update Redis hash
        redis_client.hset(redis_key, mapping={
            "topic_id": str(topic_id),
            "current_chunk_id": str(current_chunk.id),
            "current_page": str(current_chunk.page_no),
            "completion_percentage": str(pct),
            "time_spent_seconds": str(total_time),
            "completed_chunk_ids": json.dumps(completed_chunk_ids)
        })
        
        # Trigger async sync to PostgreSQL
        sync_progress_to_db.delay(user_id, document_id)

        # Structure/question responses are cached per user — a legacy-path answer
        # must refresh them too (the adaptive path already invalidates via
        # WeaknessAgent.record_answer).
        from app.services.response_cache import invalidate_cached
        invalidate_cached(f'resp:learning:{user_id}:')

        # Fetch or return transient progress
        progress = (
            self.db.query(UserProgress)
            .filter(UserProgress.user_id == user_id, UserProgress.document_id == document_id)
            .first()
        )
        if not progress:
            progress = UserProgress(
                user_id=user_id,
                document_id=document_id,
                topic_id=topic_id,
                current_chunk_id=current_chunk.id,
                current_page=current_chunk.page_no,
                completion_percentage=pct,
                time_spent_seconds=total_time,
            )
        else:
            progress.current_chunk_id = current_chunk.id
            progress.current_page = current_chunk.page_no
            progress.completion_percentage = pct
            progress.time_spent_seconds = total_time
            
        return progress


    def get_resume_position(self, user_id: int, document_id: int) -> Chunk | None:
        """Return the last chunk the user was on, or None to start from beginning."""
        progress = (
            self.db.query(UserProgress)
            .filter(UserProgress.user_id == user_id, UserProgress.document_id == document_id)
            .first()
        )
        if progress and progress.current_chunk_id:
            return self.get_chunk(progress.current_chunk_id)
        return None

    # ─── MCQs ────────────────────────────────────────────────────────────────

    def get_mcq_for_chunk(self, chunk_id: int) -> MCQBank | None:
        """Pick one MCQ that belongs to this chunk (random among available)."""
        mcqs = (
            self.db.query(MCQBank)
            .filter(MCQBank.chunk_id == chunk_id)
            .all()
        )
        if not mcqs:
            return None
        return random.choice(mcqs)

    def get_all_mcqs_for_chunk(self, chunk_id: int) -> list[MCQBank]:
        return self.db.query(MCQBank).filter(MCQBank.chunk_id == chunk_id).all()

    def get_fresh_mcq_for_chunk(self, chunk_id: int, exclude_mcq_id: int | None) -> MCQBank | None:
        """Get an MCQ for this chunk, preferring one not already shown."""
        mcqs = self.get_all_mcqs_for_chunk(chunk_id)
        if not mcqs:
            return None
        if exclude_mcq_id and len(mcqs) > 1:
            alternatives = [m for m in mcqs if m.id != exclude_mcq_id]
            return random.choice(alternatives) if alternatives else random.choice(mcqs)
        return random.choice(mcqs)

    # ─── Answer Evaluation ───────────────────────────────────────────────────

    def evaluate_answer(
        self,
        chunk_id: int,
        mcq_id: int,
        selected_option: str,
    ) -> dict:
        mcq = self.db.query(MCQBank).filter(MCQBank.id == mcq_id).first()
        if not mcq:
            raise ValueError(f"MCQ {mcq_id} not found")

        chunk = self.get_chunk(chunk_id)
        is_correct = mcq.correct_option == selected_option.upper()

        result = {
            "is_correct": is_correct,
            "correct_option": mcq.correct_option,
            "explanation": mcq.explanation or "",
            "re_explanation": None,
        }

        if not is_correct and chunk:
            result["re_explanation"] = self.rag.generate_re_explanation(
                chunk=chunk,
                wrong_option=selected_option,
                correct_option=mcq.correct_option,
            )

        return result

    # ─── On-demand MCQ Generation ─────────────────────────────────────────

    def generate_mcq_on_demand(self, chunk: Chunk) -> MCQBank | None:
        """If no MCQ exists for a chunk, generate one with Gemini and persist it."""
        from app.core.config import settings
        import time

        if not settings.gemini_api_key or settings.gemini_api_key in ("change-me", "replace-me"):
            return None

        from google import genai as genai_sdk
        from google.genai import types
        from app.utils.json_parser import parse_json_robustly
        client = genai_sdk.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=30_000),
        )

        prompt = f"""Generate exactly 1 multiple-choice question based STRICTLY on the content below.

CONTENT (Section {chunk.page_no}):
{chunk.content}

IMPORTANT: Base the question ONLY on what is written in the content above. Do not introduce any external domain, industry, or context not present in the content.

Return ONLY valid JSON (no markdown, no backticks) in this exact format:
{{
  "question": "<question text>",
  "options": {{"A": "<option A>", "B": "<option B>", "C": "<option C>", "D": "<option D>"}},
  "correct_option": "<A, B, C, or D>",
  "explanation": "<why the correct answer is right>",
  "difficulty": "medium"
}}"""

        t0 = time.monotonic()
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
            )
            text = response.text.strip()
            latency_ms = int((time.monotonic() - t0) * 1000)

            
            # Log token usage (C-2: shared estimator)
            from app.tasks.report_tasks import log_token_usage
            from app.utils.tokenizer import estimate_tokens, estimate_cost
            p_tokens = estimate_tokens(prompt)
            c_tokens = estimate_tokens(text)
            log_token_usage.delay(
                self.rag.user_id, 'mcq_gen', p_tokens, c_tokens,
                estimate_cost(p_tokens, c_tokens), False, latency_ms
            )

            data = parse_json_robustly(text)
            mcq = MCQBank(
                document_id=chunk.document_id,
                topic_id=chunk.topic_id,
                chunk_id=chunk.id,
                question=data["question"],
                options=data["options"],
                correct_option=data["correct_option"],
                explanation=data.get("explanation", ""),
                difficulty=data.get("difficulty", "medium"),
                type="objective",
            )
            self.db.add(mcq)
            self.db.commit()
            self.db.refresh(mcq)
            return mcq
        except Exception as e:
            logger.error(f"On-demand MCQ generation failed for chunk {chunk.id}: {e}")
            return None

