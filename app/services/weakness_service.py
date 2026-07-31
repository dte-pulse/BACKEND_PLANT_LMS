from sqlalchemy.orm import Session
from app.models.user_weakness_profile import UserWeaknessProfile
from app.repositories.weakness_repository import WeaknessRepository
from app.schemas.weakness import WeaknessUpdate

class WeaknessService:
    def __init__(self, db: Session):
        self.repository = WeaknessRepository(db)

    def record_weakness(self, user_id: int, payload: WeaknessUpdate):
        profile = self.repository.get_by_user_and_topic(user_id, payload.topic_id)
        if profile:
            new_count = profile.attempt_count + 1
            avg_score = ((profile.score * profile.attempt_count) + payload.score) / new_count
            is_critical = avg_score < 60.0 or (avg_score < 80.0 and new_count >= 3)
            updated = self.repository.update(
                profile,
                score=avg_score,
                attempt_count=new_count,
                is_critical=is_critical
            )
        else:
            is_critical = payload.score < 60.0
            new_profile = UserWeaknessProfile(
                user_id=user_id,
                topic_id=payload.topic_id,
                document_id=payload.document_id,
                score=payload.score,
                attempt_count=1,
                is_critical=is_critical
            )
            updated = self.repository.create(new_profile)

        # Re-queue weak content to learner path if score drops below 80%
        if updated.score < 80.0:
            from app.models.user_progress import UserProgress
            from app.models.training import TrainingAssignment
            
            # Drop progress below 100% (80% standard, or 50% if critical) to re-queue it
            progress = self.repository.db.query(UserProgress).filter(
                UserProgress.user_id == user_id,
                UserProgress.document_id == payload.document_id
            ).first()
            if progress:
                progress.completion_percentage = 80.0 if not is_critical else 50.0
                self.repository.db.commit()
                
            # Revert TrainingAssignment status to active/assigned
            assignment = self.repository.db.query(TrainingAssignment).filter(
                TrainingAssignment.user_id == user_id,
                TrainingAssignment.document_id == payload.document_id
            ).first()
            if assignment and assignment.status == 'completed':
                assignment.status = 'assigned'
                self.repository.db.commit()

        return updated

    def list_user_weaknesses(self, user_id: int):
        return self.repository.list_by_user(user_id)
