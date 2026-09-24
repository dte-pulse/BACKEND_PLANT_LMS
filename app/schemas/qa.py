from pydantic import BaseModel


class QARequest(BaseModel):
    document_id: int
    topic_id: int | None = None
    question: str
    # R-3: opt-in cross-encoder reranking for retrieval (requires
    # sentence-transformers to be installed; degrades gracefully otherwise).
    rerank: bool = False

class QAResponse(BaseModel):
    # id is None only on graceful-degradation replies (embedding outage) where
    # no session row is persisted (E-1).
    id: int | None = None
    document_id: int | None = None
    topic_id: int | None = None
    question: str
    answer: str
    source_chunk_id: int | None = None
    page_ref: int | None = None
    is_resolved: bool

    model_config = {'from_attributes': True}

class QAResolveRequest(BaseModel):
    is_resolved: bool
