from pydantic import BaseModel


class TopicCreate(BaseModel):
    subject_id: int
    title: str
    sequence_order: int = 1


class TopicUpdate(BaseModel):
    subject_id: int | None = None
    title: str | None = None
    sequence_order: int | None = None


class TopicRead(BaseModel):
    id: int
    subject_id: int
    title: str
    sequence_order: int

    model_config = {'from_attributes': True}
