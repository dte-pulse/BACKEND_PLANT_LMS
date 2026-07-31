from sqlalchemy.orm import Session

from app.models.topic import Topic


class TopicRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_all(self):
        return self.db.query(Topic).order_by(Topic.id.desc()).all()

    def get_by_id(self, topic_id: int):
        return self.db.query(Topic).filter(Topic.id == topic_id).first()

    def create(self, topic: Topic):
        self.db.add(topic)
        self.db.commit()
        self.db.refresh(topic)
        return topic

    def update(self, topic: Topic):
        self.db.commit()
        self.db.refresh(topic)
        return topic

    def delete(self, topic: Topic):
        self.db.delete(topic)
        self.db.commit()
