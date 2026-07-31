from pydantic import BaseModel
from datetime import datetime

class CalendarEventCreate(BaseModel):
    title: str
    description: str | None = None
    topic_id: int | None = None
    trainer_id: int
    start_time: datetime
    end_time: datetime
    location: str | None = None
    department: str | None = None
    calendar_type: str = 'dept_yearly'
    status: str = 'draft'
    approved_by: int | None = None
    is_carried_forward: bool = False
    carry_forward_reason: str | None = None
    actual_date: datetime | None = None
    venue: str | None = None
    trainer_remarks: str | None = None
    material_ref: str | None = None

class CalendarEventRead(BaseModel):
    id: int
    title: str
    description: str | None
    topic_id: int | None
    trainer_id: int
    start_time: datetime
    end_time: datetime
    location: str | None
    created_at: datetime
    
    department: str | None = None
    calendar_type: str
    status: str
    approved_by: int | None = None
    is_carried_forward: bool
    carry_forward_reason: str | None = None
    actual_date: datetime | None = None
    venue: str | None = None
    trainer_remarks: str | None = None
    material_ref: str | None = None
    attendance_threshold_breached: bool = False
    reschedule_triggered: bool = False

    model_config = {'from_attributes': True}

