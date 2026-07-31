from pydantic import BaseModel


class UploadDocumentResponse(BaseModel):
    document_id: int
    status: str
    file_name: str
    file_url: str
    task_name: str


class IngestionStatusResponse(BaseModel):
    document_id: int
    status: str
    chunk_count: int = 0
    mcq_count: int = 0
    summary: str | None = None
