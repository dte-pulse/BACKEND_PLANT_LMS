"""
Phase 4 — Real QA service wired to RAG with Semantic Cache and access guards.
"""
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models.document import Document
from app.models.user_qa_session import UserQaSession
from app.repositories.qa_repository import QaRepository
from app.schemas.qa import QARequest, QAResolveRequest
from app.services.rag_service import RagService
from app.services.semantic_cache_service import SemanticCacheService
from app.models.user import User, UserRole
from app.models.training import TrainingAssignment


def check_user_document_access(db: Session, user_id: int, document_id: int):
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.role == UserRole.trainee:
        assignment = db.query(TrainingAssignment).filter(
            TrainingAssignment.user_id == user_id,
            TrainingAssignment.document_id == document_id,
        ).first()
        if not assignment:
            raise HTTPException(
                status_code=403,
                detail='You do not have an assignment for this document.'
            )


class QaService:
    def __init__(self, db: Session):
        self.db = db
        self.repository = QaRepository(db)
        self.rag = RagService(db)
        self.cache = SemanticCacheService()

    def process_question(self, user_id: int, payload: QARequest):
        """
        Answer the question using Semantic Cache or RAG (pgvector retrieval + Gemini) and persist the session.
        Enforces document-scope isolation — only chunks from payload.document_id are used.
        """
        # 1. Enforce document-scope assignment authorization
        check_user_document_access(self.db, user_id, payload.document_id)

        # 2. Compute embedding for the query
        query_vec = self.rag.embedding_client.embed_text(payload.question)

        # 3. Check Semantic Cache
        document = self.db.query(Document).filter(Document.id == payload.document_id).first()
        if not document:
            raise HTTPException(status_code=404, detail='Document not found')

        topic_id = payload.topic_id if payload.topic_id is not None else document.topic_id
        if topic_id is None:
            raise HTTPException(status_code=400, detail='Topic context is missing for this document.')

        cached_answer = self.cache.lookup(
            document_id=payload.document_id,
            query_embedding=query_vec,
            topic_id=topic_id
        )

        if cached_answer is not None:
            # Cache HIT! Use cached answer and log as hit
            answer = cached_answer
            source_chunk_id = None
            page_ref = None
            context_chunks = []
            
            # Log token usage asynchronously with cache_hit=True
            from app.tasks.report_tasks import log_token_usage
            log_token_usage.delay(user_id, 'qa_cache_hit', 0, 0, 0.0, True, 0)
        else:
            # Cache MISS! Retrieve relevant chunks and generate new answer
            context_chunks = self.rag.retrieve_chunks(
                document_id=payload.document_id,
                query=payload.question,
                top_k=3,
            )

            if not context_chunks:
                from app.services.rag_service import OUT_OF_SCOPE_REPLY
                answer = OUT_OF_SCOPE_REPLY
                source_chunk_id = None
                page_ref = None
            else:
                answer = self.rag.generate_answer(payload.question, context_chunks)
                source_chunk_id = context_chunks[0].id
                page_ref = context_chunks[0].page_no

                # Store new answer in Semantic Cache
                self.cache.store(
                    document_id=payload.document_id,
                    question=payload.question,
                    query_embedding=query_vec,
                    answer=answer,
                    topic_id=topic_id
                )

        session = UserQaSession(
            user_id=user_id,
            document_id=payload.document_id,
            topic_id=topic_id,
            question=payload.question,
            answer=answer,
            source_chunk_id=source_chunk_id,
            is_resolved=False,
        )
        created = self.repository.create(session)

        # Return enriched response
        return {
            'id': created.id,
            'user_id': user_id,
            'document_id': payload.document_id,
            'topic_id': topic_id,
            'question': payload.question,
            'answer': answer,
            'source_chunk_id': source_chunk_id,
            'page_ref': page_ref,
            'source_chunks': context_chunks,
            'is_resolved': False,
        }

    def resolve_session(self, user_id: int, qa_id: int, payload: QAResolveRequest):
        qa = self.repository.get_by_id(qa_id)
        if not qa or qa.user_id != user_id:
            return None
        return self.repository.update(qa, is_resolved=payload.is_resolved)

    def list_user_qa(self, user_id: int, document_id: int | None = None):
        if document_id:
            return self.repository.list_by_user_and_document(user_id, document_id)
        return self.repository.list_by_user(user_id)
