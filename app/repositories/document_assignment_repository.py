from sqlalchemy.orm import Session
from app.models.annexure import DocumentAssignment

class DocumentAssignmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_all(self):
        return self.db.query(DocumentAssignment).order_by(DocumentAssignment.id.desc()).all()

    def get_by_id(self, assignment_id: int):
        return self.db.query(DocumentAssignment).filter(DocumentAssignment.id == assignment_id).first()

    def get_by_user_id(self, user_id: int):
        return self.db.query(DocumentAssignment).filter(DocumentAssignment.user_id == user_id).all()

    def get_by_department(self, department: str):
        return self.db.query(DocumentAssignment).filter(DocumentAssignment.department == department).all()

    def create(self, assignment: DocumentAssignment):
        self.db.add(assignment)
        self.db.commit()
        self.db.refresh(assignment)
        return assignment

    def update(self, assignment: DocumentAssignment):
        self.db.commit()
        self.db.refresh(assignment)
        return assignment

    def delete(self, assignment: DocumentAssignment):
        self.db.delete(assignment)
        self.db.commit()
