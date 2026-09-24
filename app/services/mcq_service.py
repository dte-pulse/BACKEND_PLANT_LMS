import json
import logging
import httpx
from sqlalchemy.orm import Session
from app.core.config import settings
from app.models.mcq import MCQBank
from app.models.chunk import Chunk
from app.repositories.mcq_repository import MCQRepository
from app.repositories.chunk_repository import ChunkRepository

logger = logging.getLogger(__name__)

class McqService:
    def __init__(self, db: Session):
        self.db = db
        self.mcq_repository = MCQRepository(db)
        self.chunk_repository = ChunkRepository(db)

    def list_mcqs_by_document(self, document_id: int):
        return self.mcq_repository.get_by_document(document_id)

    def update_mcq(self, mcq_id: int, payload):
        mcq = self.mcq_repository.get_by_id(mcq_id)
        if not mcq:
            raise ValueError("MCQ not found")
        return self.mcq_repository.update(mcq, **payload.dict(exclude_unset=True))

    def delete_mcq(self, mcq_id: int):
        return self.mcq_repository.delete(mcq_id)

    def generate_mcqs(self, document_id: int, count: int = 5, difficulty: str = "medium"):
        chunks = self.chunk_repository.get_by_document(document_id)
        if not chunks:
            raise ValueError("No chunks found for the document. Please ensure the document is ingested.")

        # Limit to first few chunks to avoid token limits in mock / basic setups
        selected_chunks = chunks[:min(len(chunks), 10)]
        
        # Check if we have a valid Gemini key
        use_real_api = (
            settings.gemini_api_key 
            and settings.gemini_api_key != "replace-me" 
            and settings.gemini_api_key != "change-me"
        )

        generated_mcqs = []
        
        if use_real_api:
            try:
                generated_mcqs = self._generate_with_gemini(selected_chunks, count, difficulty)
            except Exception as e:
                logger.error(f"Gemini MCQ generation failed: {e}. Falling back to mock generation.")
                generated_mcqs = self._generate_mock(selected_chunks, count, difficulty)
        else:
            generated_mcqs = self._generate_mock(selected_chunks, count, difficulty)

        # Convert generated raw data to MCQBank objects and persist them
        db_mcqs = []
        for raw in generated_mcqs:
            mcq = MCQBank(
                document_id=document_id,
                topic_id=raw["topic_id"],
                chunk_id=raw["chunk_id"],
                question=raw["question"],
                options=raw["options"],
                correct_option=raw["correct_option"],
                explanation=raw.get("explanation"),
                difficulty=difficulty,
                type=raw.get("type", "objective")
            )
            db_mcqs.append(mcq)
            
        self.mcq_repository.create_many(db_mcqs)
        return db_mcqs

    def _generate_with_gemini(self, chunks: list[Chunk], count: int, difficulty: str) -> list[dict]:
        from google import genai as genai_sdk
        from google.genai import types
        from app.utils.json_parser import parse_json_robustly
        client = genai_sdk.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=30_000),
        )
        
        chunks_context = ""
        for c in chunks:
            chunks_context += f"[Chunk ID: {c.id}, Topic ID: {c.topic_id}, Page: {c.page_no}]\n{c.content}\n\n"

        prompt = f"""
        You are a GMP compliance auditor and pharmaceutical trainer.
        Generate exactly {count} questions of {difficulty} difficulty based on the following SOP chunks.
        
        SOP Content Chunks:
        {chunks_context}

        For each question, select one of the following types:
        1. "objective": Multiple choice with 4 options (A, B, C, D).
        2. "true_false": True/False question (options: A: "True", B: "False").
        3. "descriptive": A short descriptive compliance question where options is exactly {{"A": "Submit written response"}} and correct_option is a model key answer description.

        Return ONLY a JSON list of questions. The JSON list must be strictly formatted as:
        [
          {{
            "chunk_id": <int representing the Chunk ID from which this question is drawn>,
            "topic_id": <int representing the Topic ID from the chunk>,
            "type": "<objective, true_false, or descriptive>",
            "question": "<The question string>",
            "options": {{
              "A": "<Option A / True / Submit written response>",
              "B": "<Option B / False / None if descriptive>",
              "C": "<Option C / None if true_false/descriptive>",
              "D": "<Option D / None if true_false/descriptive>"
            }},
            "correct_option": "<A, B, or ideal model answer text>",
            "explanation": "<Detailed explanation pointing back to the SOP content>"
          }}
        ]
        """
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        content_text = response.text.strip()
        return parse_json_robustly(content_text)

    def _generate_mock(self, chunks: list[Chunk], count: int, difficulty: str) -> list[dict]:
        # Generate realistic questions statically using chunk keywords
        results = []
        for i in range(count):
            chunk = chunks[i % len(chunks)]
            
            # Extract some words for dynamic mock generation
            words = [w for w in chunk.content.split() if len(w) > 5]
            keyword = words[0] if words else "procedure"
            keyword2 = words[1] if len(words) > 1 else "compliance"
            
            question = f"According to page {chunk.page_no} of the SOP, what is the critical requirement regarding '{keyword}' and '{keyword2}'?"
            options = {
                "A": f"Strict adherence to {keyword} protocols within defined timelines",
                "B": f"Deferred validation of {keyword2} parameters",
                "C": "Inform the HOD only in case of a major plant shutdown",
                "D": "No specific action required unless requested by the trainer"
            }
            correct_option = "A"
            explanation = f"As stated in chunk {chunk.chunk_index} on page {chunk.page_no}: '{chunk.content[:150]}...'"
            
            results.append({
                "chunk_id": chunk.id,
                "topic_id": chunk.topic_id,
                "question": question,
                "options": options,
                "correct_option": correct_option,
                "explanation": explanation
            })
            
        return results

    def submit_assessment(self, user_id: int, payload) -> dict:
        from app.models.user_mcq_attempt import UserMcqAttempt
        from app.schemas.weakness import WeaknessUpdate
        from app.services.weakness_service import WeaknessService
        
        weakness_service = WeaknessService(self.db)
        
        total_questions = len(payload.submissions)
        if total_questions == 0:
            raise ValueError("No submissions provided")
            
        correct_answers = 0
        feedback = []
        
        for submission in payload.submissions:
            mcq = self.mcq_repository.get_by_id(submission.mcq_id)
            if not mcq:
                raise ValueError(f"MCQ with id {submission.mcq_id} not found")
                
            # A-7: normalise the selected option so a lowercase 'a' is not
            # counted wrong (matches the adaptive path which does .upper()).
            is_correct = mcq.correct_option == submission.selected_option.upper()
            if is_correct:
                correct_answers += 1
            else:
                # Record weakness for this topic
                weakness_service.record_weakness(
                    user_id,
                    WeaknessUpdate(
                        topic_id=mcq.topic_id,
                        document_id=mcq.document_id,
                        score=0.0
                    )
                )
                
            feedback.append({
                "mcq_id": mcq.id,
                "is_correct": is_correct,
                "correct_option": mcq.correct_option,
                "explanation": mcq.explanation
            })
            
        score = (correct_answers / total_questions) * 100
        passed = score >= 80.0
        
        attempt = UserMcqAttempt(
            user_id=user_id,
            document_id=payload.document_id,
            score=score,
            passed=passed,
            duration_seconds=payload.duration_seconds,
            attempt_data={"feedback": feedback}
        )
        self.db.add(attempt)
        self.db.commit()

        # MCQ attempt + possible assignment-status change → training-record,
        # dashboard and NQ reports are stale for this user.
        from app.services.response_cache import invalidate_cached
        invalidate_cached('resp:report:', f'resp:learning:{user_id}:')

        # Trigger retraining (30 days) for NQ users (score < 80%)
        if not passed:
            from datetime import datetime, timedelta
            from app.models.training import TrainingAssignment
            existing_assignment = self.db.query(TrainingAssignment).filter(
                TrainingAssignment.user_id == user_id,
                TrainingAssignment.document_id == payload.document_id
            ).first()
            if existing_assignment:
                existing_assignment.status = "assigned"
                existing_assignment.due_date = datetime.utcnow() + timedelta(days=30)
            else:
                new_assignment = TrainingAssignment(
                    user_id=user_id,
                    document_id=payload.document_id,
                    training_type="sop",
                    status="assigned",
                    due_date=datetime.utcnow() + timedelta(days=30)
                )
                self.db.add(new_assignment)
            self.db.commit()
        
        # If passed, we might also record a 100% score for weakness to balance it out
        if passed:
             for submission in payload.submissions:
                 mcq = self.mcq_repository.get_by_id(submission.mcq_id)
                 if mcq:
                    weakness_service.record_weakness(
                        user_id,
                        WeaknessUpdate(
                            topic_id=mcq.topic_id,
                            document_id=mcq.document_id,
                            score=100.0
                        )
                    )
             # Complete the training assignment
             from app.models.training import TrainingAssignment
             assignment = self.db.query(TrainingAssignment).filter(
                 TrainingAssignment.user_id == user_id,
                 TrainingAssignment.document_id == payload.document_id
             ).first()
             if assignment:
                 if assignment.training_type == "ojt":
                     assignment.status = "pending_verification"
                 else:
                     assignment.status = "completed"
                 self.db.commit()
        
        return {
            "score": score,
            "passed": passed,
            "total_questions": total_questions,
            "correct_answers": correct_answers,
            "feedback": feedback
        }

    def get_final_assessment(self, document_id: int, count: int = 10):
        """A-8: sample exams with topic balance instead of a flat random draw,
        so exam composition is representative of the document's topics."""
        return self._balanced_sample(self.list_mcqs_by_document(document_id), count)

    def get_effectiveness_exam(self, document_id: int, count: int = 15):
        """A-8: same topic-balanced sampling as the final assessment."""
        return self._balanced_sample(self.list_mcqs_by_document(document_id), count)

    @staticmethod
    def _balanced_sample(mcqs, count: int):
        """Stratified random sample: proportionally allocate slots per topic,
        then fill any leftover slots from the overall pool."""
        import random
        if len(mcqs) <= count:
            return mcqs

        by_topic: dict = {}
        for m in mcqs:
            by_topic.setdefault(m.topic_id, []).append(m)

        sample = []
        # proportional floor per topic
        for topic_id, topic_mcqs in by_topic.items():
            share = int(round(len(topic_mcqs) / len(mcqs) * count))
            sample.extend(random.sample(topic_mcqs, min(share, len(topic_mcqs))))

        # fill remaining slots from the overall pool (excluding already picked)
        picked_ids = {m.id for m in sample}
        pool = [m for m in mcqs if m.id not in picked_ids]
        if len(sample) < count:
            sample.extend(random.sample(pool, count - len(sample)))
        return sample[:count]

