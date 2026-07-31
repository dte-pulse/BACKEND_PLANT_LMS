from pydantic import BaseModel


class DepartmentCreate(BaseModel):
    name: str
    code: str | None = None
    description: str | None = None
    head_name: str | None = None


class DepartmentUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    head_name: str | None = None
    is_active: bool | None = None


class DepartmentRead(BaseModel):
    id: int
    name: str
    code: str | None = None
    description: str | None = None
    head_name: str | None = None
    is_active: bool

    model_config = {'from_attributes': True}
