from pydantic import BaseModel


class DocumentCreate(BaseModel):
    code: str
    title: str
    topic: str
    topic_id: int | None = None
    subject_id: int | None = None
    version: int = 1
    sequence_order: int = 1
    status: str = 'draft'
    qa_scope: str = 'doc_strict'


class DocumentUpdate(BaseModel):
    title: str | None = None
    topic: str | None = None
    topic_id: int | None = None
    subject_id: int | None = None
    sequence_order: int | None = None
    status: str | None = None
    qa_scope: str | None = None


class DocumentRead(BaseModel):
    id: int
    code: str
    title: str
    topic: str
    topic_id: int | None = None
    subject_id: int | None = None
    version: int
    sequence_order: int
    status: str
    file_name: str | None = None
    file_type: str | None = None
    file_url: str | None = None
    qa_scope: str
    summary: str | None = None

    model_config = {'from_attributes': True}
