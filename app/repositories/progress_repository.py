from sqlalchemy.orm import Session

from app.models.user_progress import UserProgress


class ProgressRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_user_progress(self, user_id: int, document_id: int):
        return self.db.query(UserProgress).filter(
            UserProgress.user_id == user_id, 
            UserProgress.document_id == document_id
        ).first()

    def create(self, progress: UserProgress):
        self.db.add(progress)
        self.db.commit()
        self.db.refresh(progress)
        return progress

    def update(self, progress: UserProgress, **kwargs):
        for key, value in kwargs.items():
            setattr(progress, key, value)
        self.db.add(progress)
        self.db.commit()
        self.db.refresh(progress)
        return progress

    def list_by_user(self, user_id: int):
        return self.db.query(UserProgress).filter(UserProgress.user_id == user_id).all()
