from datetime import datetime
from pydantic import BaseModel
from typing import Optional


class DocumentAssignmentCreate(BaseModel):
    document_id: int
    user_id: Optional[int] = None
    department: Optional[str] = None
    due_date: Optional[datetime] = None
    is_mandatory: bool = True


class DocumentAssignmentUpdate(BaseModel):
    due_date: Optional[datetime] = None
    is_mandatory: Optional[bool] = None


class DocumentAssignmentRead(BaseModel):
    id: int
    document_id: int
    user_id: Optional[int] = None
    department: Optional[str] = None
    assigned_by: Optional[int] = None
    due_date: Optional[datetime] = None
    is_mandatory: bool
    created_at: datetime

    model_config = {'from_attributes': True}
