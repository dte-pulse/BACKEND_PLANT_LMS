from app.core.security import get_password_hash
from app.models.user import User, UserRole
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate


class UserService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    def create_user(self, payload: UserCreate):
        user = User(
            employee_code=payload.employee_code,
            full_name=payload.full_name,
            email=payload.email,
            hashed_password=get_password_hash(payload.password),
            department=payload.department,
            role=UserRole(payload.role),
            employee_type=payload.employee_type,
        )
        created = self.repository.create(user)
        
        # If the user is a trainee, assign default induction SOPs
        if created.role == UserRole.trainee:
            from app.models.document import Document
            from app.models.training import TrainingAssignment
            from app.schemas.training import TrainingAssignmentCreate
            from app.services.training_service import TrainingService
            from app.repositories.training_repository import TrainingRepository
            from datetime import datetime, timedelta
            
            # Find all documents under the 'induction' topic or general induction
            induction_docs = self.repository.db.query(Document).filter(
                Document.topic.ilike('%induction%')
            ).all()
            
            # If no specific induction docs exist, fallback to general active documents
            if not induction_docs:
                induction_docs = self.repository.db.query(Document).filter(
                    Document.status == 'active'
                ).limit(3).all()
                
            training_service = TrainingService(TrainingRepository(self.repository.db))
            for doc in induction_docs:
                training_service.create_assignment(TrainingAssignmentCreate(
                    user_id=created.id,
                    document_id=doc.id,
                    training_type="induction",
                    status="assigned",
                    due_date=datetime.utcnow() + timedelta(days=30)
                ))

        self._invalidate_user_caches()
        return created

    def list_users(self, *, department: str | None = None, role: str | None = None):
        return self.repository.list_all(department=department, role=role)

    @staticmethod
    def _invalidate_user_caches():
        """User roster changed → compliance/nq reports are stale."""
        from app.services.response_cache import invalidate_cached
        invalidate_cached('resp:report:')
