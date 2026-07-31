from datetime import datetime

from pydantic import BaseModel


class TrainingEvidenceRead(BaseModel):
    id: int
    assignment_id: int
    uploaded_by_id: int | None
    label: str | None
    file_name: str
    file_type: str | None
    download_url: str
    created_at: datetime | None

    model_config = {'from_attributes': True}
