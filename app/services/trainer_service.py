"""
Phase 5 — TrainerService: full CRUD + qualification management.
"""
from sqlalchemy.orm import Session
from app.models.trainer import Trainer
from app.models.user import User


class TrainerService:
    def __init__(self, db: Session):
        self.db = db

    def list_trainers(self):
        """Return all approved trainers with their user profile."""
        return (
            self.db.query(Trainer, User)
            .join(User, Trainer.user_id == User.id)
            .filter(User.is_active == True)
            .all()
        )

    def get_trainer_by_user_id(self, user_id: int):
        return self.db.query(Trainer).filter(Trainer.user_id == user_id).first()

    def register_trainer(self, user_id: int, specialization: str, qualification: str, bio: str | None = None):
        """Create a trainer profile for a user with trainer role."""
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError(f'User {user_id} not found')
        existing = self.get_trainer_by_user_id(user_id)
        if existing:
            raise ValueError('Trainer profile already exists for this user')
        trainer = Trainer(
            user_id=user_id,
            specialization=specialization,
            qualification=qualification,
            bio=bio,
            is_approved=False,
        )
        self.db.add(trainer)
        self.db.commit()
        self.db.refresh(trainer)
        return trainer

    def approve_trainer(self, trainer_id: int, approved_by: int):
        trainer = self.db.query(Trainer).filter(Trainer.id == trainer_id).first()
        if not trainer:
            raise ValueError('Trainer not found')
        trainer.is_approved = True
        trainer.approved_by = approved_by
        self.db.commit()
        self.db.refresh(trainer)
        return trainer

    def get_trainer_assignments(self, user_id: int):
        """Get all training assignments where this user is the trainer."""
        from app.models.training import TrainingAssignment
        from app.models.user import User as UserModel
        from app.models.document import Document

        assignments = (
            self.db.query(TrainingAssignment)
            .filter(TrainingAssignment.trainer_id == user_id)
            .order_by(TrainingAssignment.created_at.desc())
            .all()
        )
        result = []
        for a in assignments:
            trainee = self.db.query(UserModel).filter(UserModel.id == a.user_id).first()
            doc = self.db.query(Document).filter(Document.id == a.document_id).first() if a.document_id else None
            result.append({
                'assignment_id': a.id,
                'trainee_id': a.user_id,
                'trainee_name': trainee.full_name if trainee else None,
                'trainee_code': trainee.employee_code if trainee else None,
                'department': trainee.department if trainee else None,
                'training_type': a.training_type,
                'document_title': doc.title if doc else None,
                'document_code': doc.code if doc else None,
                'status': a.status,
                'verified_by_trainer': a.verified_by_trainer,
                'due_date': a.due_date,
                'created_at': a.created_at,
            })
        return result

    def get_trainer_dashboard(self, user_id: int):
        """Summary stats for the trainer dashboard."""
        from app.models.training import TrainingAssignment

        total = self.db.query(TrainingAssignment).filter(
            TrainingAssignment.trainer_id == user_id
        ).count()
        completed = self.db.query(TrainingAssignment).filter(
            TrainingAssignment.trainer_id == user_id,
            TrainingAssignment.status == 'completed',
        ).count()
        pending_verification = self.db.query(TrainingAssignment).filter(
            TrainingAssignment.trainer_id == user_id,
            TrainingAssignment.status == 'pending_verification',
        ).count()
        return {
            'total_assigned': total,
            'completed': completed,
            'pending': total - completed,
            'pending_verification': pending_verification,
        }
