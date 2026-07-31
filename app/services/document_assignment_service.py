from sqlalchemy.orm import Session
from app.repositories.document_assignment_repository import DocumentAssignmentRepository
from app.models.annexure import DocumentAssignment
from app.models.user import User
from app.schemas.document_assignment import DocumentAssignmentCreate, DocumentAssignmentUpdate
from app.services.training_service import TrainingService
from app.repositories.training_repository import TrainingRepository
from app.schemas.training import TrainingAssignmentCreate
from app.services.notification_service import NotificationService

class DocumentAssignmentService:
    def __init__(self, db: Session):
        self.db = db
        self.repository = DocumentAssignmentRepository(db)
        self.training_service = TrainingService(TrainingRepository(db))
        self.notification_service = NotificationService(db)

    def create_assignment(self, payload: DocumentAssignmentCreate, assigned_by_id: int):
        # 1. Create DocumentAssignment entry
        assignment = DocumentAssignment(
            document_id=payload.document_id,
            user_id=payload.user_id,
            department=payload.department,
            assigned_by=assigned_by_id,
            due_date=payload.due_date,
            is_mandatory=payload.is_mandatory
        )
        created = self.repository.create(assignment)

        # 2. Replicate operational TrainingAssignment for user(s)
        target_user_ids = []
        if payload.user_id:
            target_user_ids.append(payload.user_id)
        elif payload.department:
            # Query all active trainees/users in this department
            users = self.db.query(User).filter(
                User.department == payload.department,
                User.is_active == True
            ).all()
            target_user_ids.extend([u.id for u in users])

        for uid in target_user_ids:
            # Check if training assignment already exists to prevent duplicate assignments
            from app.models.training import TrainingAssignment
            exists = self.db.query(TrainingAssignment).filter(
                TrainingAssignment.user_id == uid,
                TrainingAssignment.document_id == payload.document_id
            ).first()
            if not exists:
                self.training_service.create_assignment(
                    TrainingAssignmentCreate(
                        user_id=uid,
                        document_id=payload.document_id,
                        training_type="sop",  # Default type
                        status="assigned",
                        due_date=payload.due_date
                    )
                )
            
            # Send notification
            try:
                due_str = payload.due_date.strftime('%Y-%m-%d') if payload.due_date else 'TBD'
                self.notification_service.notify_training_due(uid, "Document Assignment", due_str)
            except Exception:
                pass

        return created

    def list_assignments(self):
        return self.repository.list_all()

    def get_assignment(self, assignment_id: int):
        return self.repository.get_by_id(assignment_id)

    def get_user_assignments(self, user_id: int):
        # Include both direct assignments and department assignments
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return []
        
        direct = self.repository.get_by_user_id(user_id)
        dept = []
        if user.department:
            dept = self.repository.get_by_department(user.department)
        
        return list(set(direct + dept))

    def update_assignment(self, assignment_id: int, payload: DocumentAssignmentUpdate):
        assignment = self.repository.get_by_id(assignment_id)
        if not assignment:
            return None
        
        if payload.due_date is not None:
            assignment.due_date = payload.due_date
        if payload.is_mandatory is not None:
            assignment.is_mandatory = payload.is_mandatory

        return self.repository.update(assignment)

    def delete_assignment(self, assignment_id: int):
        assignment = self.repository.get_by_id(assignment_id)
        if not assignment:
            return False
        self.repository.delete(assignment)
        return True
