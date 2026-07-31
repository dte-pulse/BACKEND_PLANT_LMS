from pydantic import BaseModel

class ChunkRead(BaseModel):
    id: int
    document_id: int
    topic_id: int
    subject_id: int
    page_no: int
    chunk_index: int
    token_count: int
    content: str
    learning_card: str | None = None

    model_config = {'from_attributes': True}
