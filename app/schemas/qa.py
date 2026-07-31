from pydantic import BaseModel


class QARequest(BaseModel):
    document_id: int
    topic_id: int | None = None
    question: str

class QAResponse(BaseModel):
    id: int
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
