from pydantic import BaseModel
from datetime import datetime


class TrainingAssignmentCreate(BaseModel):
    user_id: int
    document_id: int | None = None
    training_type: str
    status: str = 'assigned'
    trainer_id: int | None = None
    due_date: datetime | None = None
    certificate_url: str | None = None
    assigned_by_id: int | None = None
    approved_by_id: int | None = None
    notes: str | None = None
    approval_notes: str | None = None
    requested_reason: str | None = None
    external_provider: str | None = None
    external_venue: str | None = None
    external_duration_hours: int | None = None
    approved_at: datetime | None = None


class TrainingAssignmentRead(BaseModel):
    id: int
    user_id: int
    document_id: int | None
    training_type: str
    status: str
    trainer_id: int | None
    verified_by_trainer: bool
    certificate_url: str | None
    due_date: datetime | None
    assigned_by_id: int | None
    approved_by_id: int | None
    notes: str | None
    approval_notes: str | None
    requested_reason: str | None
    external_provider: str | None
    external_venue: str | None
    external_duration_hours: int | None
    approved_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = {'from_attributes': True}
