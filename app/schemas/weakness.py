from pydantic import BaseModel

class WeaknessUpdate(BaseModel):
    topic_id: int
    document_id: int
    score: float
    is_critical: bool = False

class WeaknessRead(BaseModel):
    id: int
    user_id: int
    topic_id: int
    document_id: int
    score: float
    attempt_count: int
    is_critical: bool

    model_config = {'from_attributes': True}
