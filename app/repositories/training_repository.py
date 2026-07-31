from sqlalchemy.orm import Session

from app.models.training import TrainingAssignment
from app.models.user import User


class TrainingRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_all(self, *, department: str | None = None, user_id: int | None = None, training_type: str | None = None, status: str | None = None):
        query = self.db.query(TrainingAssignment)
        if department:
            query = query.join(User, User.id == TrainingAssignment.user_id).filter(User.department == department)
        if user_id is not None:
            query = query.filter(TrainingAssignment.user_id == user_id)
        if training_type:
            query = query.filter(TrainingAssignment.training_type == training_type)
        if status:
            query = query.filter(TrainingAssignment.status == status)
        return query.order_by(TrainingAssignment.id.desc()).all()

    def create(self, assignment: TrainingAssignment):
        self.db.add(assignment)
        self.db.commit()
        self.db.refresh(assignment)
        return assignment

    def get_by_user_id(self, user_id: int):
        return self.db.query(TrainingAssignment).filter(TrainingAssignment.user_id == user_id).order_by(TrainingAssignment.id.desc()).all()

    def get_by_id(self, assignment_id: int):
        return self.db.query(TrainingAssignment).filter(TrainingAssignment.id == assignment_id).first()

    def update(self, assignment: TrainingAssignment, **kwargs) -> TrainingAssignment:
        for key, value in kwargs.items():
            if hasattr(assignment, key):
                setattr(assignment, key, value)
        self.db.commit()
        self.db.refresh(assignment)
        return assignment

    def delete(self, assignment_id: int) -> bool:
        assignment = self.get_by_id(assignment_id)
        if not assignment:
            return False
        self.db.delete(assignment)
        self.db.commit()
        return True
