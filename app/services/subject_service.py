from app.models.subject import Subject
from app.repositories.subject_repository import SubjectRepository
from app.schemas.subject import SubjectCreate, SubjectUpdate


class SubjectService:
    def __init__(self, repository: SubjectRepository):
        self.repository = repository

    def create_subject(self, payload: SubjectCreate):
        subject = Subject(name=payload.name, department=payload.department)
        return self.repository.create(subject)

    def list_subjects(self):
        return self.repository.list_all()

    def get_subject(self, subject_id: int):
        return self.repository.get_by_id(subject_id)

    def update_subject(self, subject_id: int, payload: SubjectUpdate):
        subject = self.repository.get_by_id(subject_id)
        if not subject:
            return None
        if payload.name is not None:
            subject.name = payload.name
        if payload.department is not None:
            subject.department = payload.department
        return self.repository.update(subject)

    def delete_subject(self, subject_id: int):
        subject = self.repository.get_by_id(subject_id)
        if not subject:
            return False
        self.repository.delete(subject)
        return True
