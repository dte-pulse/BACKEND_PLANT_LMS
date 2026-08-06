import logging
from sqlalchemy.orm import Session

from app.clients.embedding_client import EmbeddingClient
from app.clients.llm_client import LLMClient
from app.models.document import Document
from app.models.chunk import Chunk
from app.models.mcq import MCQBank
from app.models.parent_chunk import ParentChunk
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.mcq_repository import MCQRepository
from app.services.chunking_service import ChunkingService
from app.services.extraction_service import ExtractionService

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self, db: Session):
        self.db = db
        self.document_repository = DocumentRepository(db)
        self.chunk_repository = ChunkRepository(db)
        self.mcq_repository = MCQRepository(db)
        self.extraction_service = ExtractionService()
        self.chunking_service = ChunkingService()
        self.embedding_client = EmbeddingClient()
        self.llm_client = LLMClient()

    def process_document(self, document_id: int):
        document = self.document_repository.get_by_id(document_id)
        if not document:
            raise ValueError('Document not found')

        # ── Clean up old data — FK-safe deletion order ─────────────────────
        # Dependency graph (child → parent):
        #   child_chunk_attempts  → mcq_bank, chunks, parent_chunks, documents
        #   parent_chunk_progress → parent_chunks, documents
        #   user_mcq_attempts     → documents
        #   mcq_bank              → chunks, documents
        #   chunks                → parent_chunks, documents
        #   parent_chunks         → documents
        from app.models.child_chunk_attempt import ChildChunkAttempt
        from app.models.parent_chunk_progress import ParentChunkProgress
        from app.models.user_mcq_attempt import UserMcqAttempt
        from app.models.user_qa_session import UserQaSession
        from app.models.user_progress import UserProgress

        # Detach foreign keys on user_qa_sessions & user_progress before deleting old chunks
        self.db.query(UserQaSession).filter(UserQaSession.document_id == document.id).update({'source_chunk_id': None})
        self.db.query(UserProgress).filter(UserProgress.document_id == document.id).update({'current_chunk_id': None})

        self.db.query(ChildChunkAttempt).filter(ChildChunkAttempt.document_id == document.id).delete()
        self.db.query(ParentChunkProgress).filter(ParentChunkProgress.document_id == document.id).delete()
        self.db.query(UserMcqAttempt).filter(UserMcqAttempt.document_id == document.id).delete()
        self.db.query(MCQBank).filter(MCQBank.document_id == document.id).delete()
        self.db.query(Chunk).filter(Chunk.document_id == document.id).delete()
        self.db.query(ParentChunk).filter(ParentChunk.document_id == document.id).delete()
        self.db.commit()

        # ── Invalidate Semantic Cache ───────────────────────────────────────
        from app.services.semantic_cache_service import SemanticCacheService
        try:
            cache = SemanticCacheService()
            cache.invalidate_document(document.id)
        except Exception:
            pass

        # ── Phase 1: Extract + Chunk ────────────────────────────────────────
        try:
            self.document_repository.update(document, status='chunking', failure_reason=None)
            from app.storage.file_storage import FileStorageService
            storage = FileStorageService()
            download_url = document.file_url
            if document.file_name and storage.use_s3:
                presigned = storage.get_presigned_url(document.file_name)
                if presigned:
                    download_url = presigned

            extracted = self.extraction_service.extract(download_url, document.file_type)
            pages = extracted['pages']
            toc = extracted['toc']
            chunk_result = self.chunking_service.split_pages(pages, toc=toc)

            # Validate that extraction produced usable content
            if not chunk_result.get('parents'):
                failure_msg = 'Document produced no content — it may be scanned, image-only, encrypted, or empty.'
                self.document_repository.update(document, status='failed_extraction', failure_reason=failure_msg)
                raise ValueError(failure_msg)

        except Exception as e:
            self.db.rollback()
            if document.status != 'failed_extraction':
                self.document_repository.update(document, status='failed_extraction', failure_reason=str(e))
            raise e

        # ── Phase 2: Embed + Persist Parent & Child Chunks ─────────────────
        try:
            self.document_repository.update(document, status='embedding')
            all_child_chunks = []  # will hold persisted Chunk ORM objects

            # Find previous version of the document to do section-level diff
            prev_doc = self.db.query(Document).filter(
                Document.code == document.code,
                Document.id != document.id
            ).order_by(Document.version.desc()).first()

            prev_parents_map = {}
            prev_children_map = {}
            if prev_doc:
                prev_parents = self.db.query(ParentChunk).filter(ParentChunk.document_id == prev_doc.id).all()
                for p in prev_parents:
                    if p.stable_id:
                        prev_parents_map[p.stable_id] = p
                
                prev_children = self.db.query(Chunk).filter(Chunk.document_id == prev_doc.id).all()
                for c in prev_children:
                    if c.stable_id:
                        prev_children_map[c.stable_id] = c

            import hashlib
            import re

            def sanitize_id_part(text: str) -> str:
                if not text:
                    return ""
                text = re.sub(r'[^a-zA-Z0-9\s-]', '', text)
                text = re.sub(r'[\s-]+', '_', text)
                return text.strip('_').lower()

            def compute_hash(text: str) -> str:
                return hashlib.sha256(text.encode('utf-8')).hexdigest()

            # Pre-calculate stable_id and content_hash for all parents and children in chunk_result
            for parent_data in chunk_result.get('parents', []):
                p_title_sanitized = sanitize_id_part(parent_data['title'])
                if not p_title_sanitized:
                    p_title_sanitized = f"sec_{parent_data['section_index']}"
                parent_stable_id = f"{document.code}__{p_title_sanitized}"
                parent_data['stable_id'] = parent_stable_id
                parent_data['content_hash'] = compute_hash(parent_data['content'])

                for child_data in parent_data.get('children', []):
                    child_stable_id = f"{parent_stable_id}__c_{child_data['child_index']:02d}"
                    child_data['stable_id'] = child_stable_id
                    child_data['content_hash'] = compute_hash(child_data['content'])

            for parent_data in chunk_result.get('parents', []):
                parent_stable_id = parent_data['stable_id']
                p_hash = parent_data['content_hash']

                prev_parent = prev_parents_map.get(parent_stable_id)
                is_parent_unchanged = prev_parent and prev_parent.content_hash == p_hash

                if is_parent_unchanged:
                    parent_embedding = prev_parent.embedding
                    parent_summary = prev_parent.summary
                else:
                    parent_embedding = self.embedding_client.embed_text(
                        parent_data['content'][:8000]
                    )
                    parent_summary = None

                parent_obj = ParentChunk(
                    document_id=document.id,
                    topic_id=document.topic_id or 0,
                    subject_id=document.subject_id or 0,
                    section_index=parent_data['section_index'],
                    title=parent_data['title'],
                    content=parent_data['content'],
                    summary=parent_summary,
                    embedding=parent_embedding,
                    page_start=parent_data['page_start'],
                    page_end=parent_data['page_end'],
                    token_count=parent_data['token_count'],
                    stable_id=parent_stable_id,
                    content_hash=p_hash
                )
                self.db.add(parent_obj)
                self.db.flush()  # get parent_obj.id before creating children

                # Create child chunks under this parent
                for child_data in parent_data.get('children', []):
                    child_stable_id = child_data['stable_id']
                    c_hash = child_data['content_hash']

                    prev_child = prev_children_map.get(child_stable_id)
                    is_child_unchanged = prev_child and prev_child.content_hash == c_hash

                    if is_child_unchanged:
                        child_embedding = prev_child.embedding
                        child_learning_card = prev_child.learning_card
                    else:
                        child_embedding = self.embedding_client.embed_text(
                            child_data['content']
                        )
                        child_learning_card = self.llm_client.generate_learning_card(
                            child_data['content']
                        )

                    child_obj = Chunk(
                        document_id=document.id,
                        topic_id=document.topic_id or 0,
                        subject_id=document.subject_id or 0,
                        parent_chunk_id=parent_obj.id,
                        child_index=child_data['child_index'],
                        chunk_index=child_data['chunk_index'],
                        chunk_type='child',
                        page_no=child_data['page_no'],
                        token_count=child_data['token_count'],
                        content=child_data['content'],
                        learning_card=child_learning_card,
                        embedding=child_embedding,
                        stable_id=child_stable_id,
                        content_hash=c_hash
                    )
                    self.db.add(child_obj)
                    all_child_chunks.append(child_obj)

            self.db.commit()
            # Refresh all children to get their IDs, then snapshot into plain
            # dicts so Phase 3 never touches expired ORM objects after commit.
            chunk_snapshots = []
            for c in all_child_chunks:
                self.db.refresh(c)
                chunk_snapshots.append({'id': c.id, 'content': c.content})

        except Exception as e:
            self.db.rollback()
            self.document_repository.update(document, status='failed_embedding', failure_reason=str(e))
            raise e

        # ── Phase 3: MCQ Generation (easy + medium + hard per child) ────────
        try:
            self.document_repository.update(document, status='mcq_gen')
            mcq_rows = []

            for i, snap in enumerate(chunk_snapshots):
                child_db_obj = all_child_chunks[i]
                c_stable_id = child_db_obj.stable_id
                c_hash = child_db_obj.content_hash

                prev_child = prev_children_map.get(c_stable_id)
                is_child_unchanged = prev_child and prev_child.content_hash == c_hash

                copied_mcqs = False
                if is_child_unchanged:
                    # Fetch MCQs from previous child
                    prev_mcqs = self.db.query(MCQBank).filter(MCQBank.chunk_id == prev_child.id).all()
                    if prev_mcqs:
                        for prev_mcq in prev_mcqs:
                            mcq_rows.append(
                                MCQBank(
                                    document_id=document.id,
                                    topic_id=document.topic_id or 0,
                                    chunk_id=child_db_obj.id,
                                    question=prev_mcq.question,
                                    options=prev_mcq.options,
                                    correct_option=prev_mcq.correct_option,
                                    explanation=prev_mcq.explanation,
                                    difficulty=prev_mcq.difficulty,
                                    type=prev_mcq.type
                                )
                            )
                        copied_mcqs = True

                if not copied_mcqs:
                    items = self._generate_tiered_mcqs(snap['content'])
                    if not items:
                        # Guarantee MCQs even when Gemini is unavailable / quota-exceeded
                        items = self._fallback_mcqs_for_content(snap['content'])

                    for item in items:
                        mcq_rows.append(
                            MCQBank(
                                document_id=document.id,
                                topic_id=document.topic_id or 0,
                                chunk_id=child_db_obj.id,
                                question=item['question'],
                                options=item['options'],
                                correct_option=item['correct_option'],
                                explanation=item['explanation'],
                                difficulty=item['difficulty'],
                                type=item.get('type', 'objective'),
                            )
                        )


            if mcq_rows:
                self.mcq_repository.create_many(mcq_rows)

            # Generate and save document summary & NotebookLM-style mind map concept tree
            summary = self.llm_client.generate_topic_summary(
                [snap['content'] for snap in chunk_snapshots]
            )
            
            # Check versioning and perform comparison if a previous version exists
            try:
                prev_doc = self.db.query(Document).filter(
                    Document.code == document.code,
                    Document.id != document.id
                ).order_by(Document.version.desc()).first()

                if prev_doc:
                    # Retrieve the full text of the previous version from its parents
                    prev_parents = self.db.query(ParentChunk).filter(
                        ParentChunk.document_id == prev_doc.id
                    ).order_by(ParentChunk.section_index).all()
                    
                    prev_text = "\n\n".join(p.content for p in prev_parents)
                    new_text = "\n\n".join(snap['content'] for snap in chunk_snapshots)
                    
                    if prev_text.strip():
                        changelog = self.llm_client.compare_document_versions(prev_text, new_text)
                        summary = f"{summary}\n\n### Version {document.version} Changelog (Changes from Version {prev_doc.version}):\n{changelog}"
            except Exception as comp_err:
                logger.warning(f"Failed to generate revision changelog comparison: {comp_err}")

            outline_text = "\n\n".join(
                f"Section: {p.get('title', '')}\n{p.get('content', '')[:1000]}"
                for p in chunk_result.get('parents', [])
            )
            mind_map_tree = self.llm_client.generate_mind_map_structure(outline_text, document.title)
            self.document_repository.update(document, status='ready', summary=summary, mind_map_json=mind_map_tree)

        except Exception as e:
            self.db.rollback()
            self.document_repository.update(document, status='failed_mcq', failure_reason=str(e))
            raise e

        return {
            'document_id': document.id,
            'status': 'ready',
            'parent_count': len(chunk_result.get('parents', [])),
            'chunk_count': len(all_child_chunks),
            'mcq_count': len(mcq_rows),
            'summary': summary,
            'failure_reason': None,
        }

    def _generate_tiered_mcqs(self, content: str) -> list[dict]:
        """
        Generate 3 MCQs (easy, medium, hard) for a child chunk.
        Tries llm_client first. Falls back to individual Gemini calls if needed.
        """
        from app.core.config import settings
        import json, time

        if not settings.gemini_api_key or settings.gemini_api_key in ('change-me', 'replace-me'):
            return self._fallback_mcqs()

        prompt = f"""You are a subject matter expert. Generate exactly 3 multiple-choice questions based STRICTLY on the content below — one EASY (recall), one MEDIUM (application), one HARD (analysis/scenario).

CONTENT:
{content[:3500]}

IMPORTANT: Base every question ONLY on the content above. Do not introduce any external domain, industry, or context not present in the content.

Return ONLY a valid JSON array of 3 objects (no markdown, no backticks):
[
  {{"question": "<easy question>", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "<A/B/C/D>", "explanation": "<why correct>", "difficulty": "easy", "type": "objective"}},
  {{"question": "<medium question>", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "<A/B/C/D>", "explanation": "<why correct>", "difficulty": "medium", "type": "objective"}},
  {{"question": "<hard question>", "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}}, "correct_option": "<A/B/C/D>", "explanation": "<why correct>", "difficulty": "hard", "type": "objective"}}
]"""

        try:
            from google import genai as genai_sdk
            client = genai_sdk.Client(api_key=settings.gemini_api_key)
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
            )
            text = response.text.strip()
            if text.startswith('```'):
                lines = text.split('\n')
                text = '\n'.join(lines[1:-1])
            data = json.loads(text)
            if isinstance(data, list) and len(data) == 3:
                return data
        except Exception as e:
            logger.error(f'Tiered MCQ generation failed: {e}')

        # Try llm_client fallback (generates variable number, label them medium)
        try:
            items = self.llm_client.generate_mcqs(content)
            return items[:3] if items else self._fallback_mcqs()
        except Exception:
            return self._fallback_mcqs()

    def _fallback_mcqs_for_content(self, content: str) -> list[dict]:
        """Generates clean, contextual MCQs based on chunk text immediately."""
        clean_text = (content or "").replace('\n', ' ').strip()
        first_sent = clean_text.split('.')[0][:100] if clean_text else "document procedures"
        return [
            {
                'question': f"What is the primary focus regarding: '{first_sent}'?",
                'options': {
                    'A': 'Follow the standard operating protocol strictly as specified.',
                    'B': 'Bypass procedure guidelines for speed.',
                    'C': 'Modify the operating procedure without authorization.',
                    'D': 'Discard document instructions.'
                },
                'correct_option': 'A',
                'explanation': f"The document requires strict compliance with guidelines regarding {first_sent}.",
                'difficulty': 'easy',
                'type': 'objective',
            },
            {
                'question': f"Which action is compliant with: '{first_sent}'?",
                'options': {
                    'A': 'Adhere to documented parameters and safety checks.',
                    'B': 'Skip verification steps.',
                    'C': 'Use unverified third-party tools.',
                    'D': 'Ignore safety compliance.'
                },
                'correct_option': 'A',
                'explanation': 'Compliance requires adherence to documented parameters and safety checks.',
                'difficulty': 'medium',
                'type': 'objective',
            },
            {
                'question': f"In a non-conformance scenario involving '{first_sent}', what is required?",
                'options': {
                    'A': 'Log a deviation report and notify the supervisor immediately.',
                    'B': 'Conceal the anomaly to avoid delays.',
                    'C': 'Proceed without documentation.',
                    'D': 'Delete the audit trail record.'
                },
                'correct_option': 'A',
                'explanation': 'Standard protocol mandates logging a deviation report and notifying the supervisor.',
                'difficulty': 'hard',
                'type': 'objective',
            }
        ]

    def get_status(self, document_id: int):
        document = self.document_repository.get_by_id(document_id)
        if not document:
            raise ValueError('Document not found')

        parent_count = self.db.query(ParentChunk).filter(
            ParentChunk.document_id == document_id
        ).count()

        return {
            'document_id': document.id,
            'status': document.status,
            'parent_count': parent_count,
            'chunk_count': self.chunk_repository.count_by_document(document_id),
            'mcq_count': self.mcq_repository.count_by_document(document_id),
            'summary': document.summary,
            'failure_reason': document.failure_reason,
        }
