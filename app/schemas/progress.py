from pydantic import BaseModel


class ProgressUpdate(BaseModel):
    document_id: int
    topic_id: int
    current_chunk_id: int | None = None
    current_page: int = 1
    completion_percentage: float = 0.0
    time_spent_seconds: int = 0

class ProgressRead(BaseModel):
    id: int
    user_id: int
    document_id: int
    topic_id: int
    current_chunk_id: int | None = None
    current_page: int
    completion_percentage: float
    time_spent_seconds: int

    model_config = {'from_attributes': True}
