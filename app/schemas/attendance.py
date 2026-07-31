from pydantic import BaseModel
from datetime import datetime

class AttendanceCreate(BaseModel):
    user_id: int
    is_present: bool

class AttendanceRead(BaseModel):
    id: int
    calendar_event_id: int
    user_id: int
    is_present: bool
    marked_by_id: int
    created_at: datetime

    model_config = {'from_attributes': True}


class AttendanceRosterRead(BaseModel):
    id: int | None = None
    calendar_event_id: int
    user_id: int
    user_name: str | None = None
    is_present: bool
    marked_by_id: int | None = None
    material_used: bool = False
    comments: str | None = None
    created_at: datetime | None = None
