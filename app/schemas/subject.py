from pydantic import BaseModel


class SubjectCreate(BaseModel):
    name: str
    department: str | None = None


class SubjectUpdate(BaseModel):
    name: str | None = None
    department: str | None = None


class SubjectRead(BaseModel):
    id: int
    name: str
    department: str | None = None

    model_config = {'from_attributes': True}
