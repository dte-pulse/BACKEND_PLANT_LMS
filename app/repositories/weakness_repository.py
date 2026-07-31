from sqlalchemy.orm import Session
from app.models.user_weakness_profile import UserWeaknessProfile

class WeaknessRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_user_and_topic(self, user_id: int, topic_id: int):
        return self.db.query(UserWeaknessProfile).filter(
            UserWeaknessProfile.user_id == user_id,
            UserWeaknessProfile.topic_id == topic_id
        ).first()

    def create(self, profile: UserWeaknessProfile):
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def update(self, profile: UserWeaknessProfile, **kwargs):
        for key, value in kwargs.items():
            setattr(profile, key, value)
        self.db.add(profile)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    def list_by_user(self, user_id: int):
        return self.db.query(UserWeaknessProfile).filter(UserWeaknessProfile.user_id == user_id).all()
