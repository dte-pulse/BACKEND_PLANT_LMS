from app.models.topic import Topic
from app.repositories.topic_repository import TopicRepository
from app.schemas.topic import TopicCreate, TopicUpdate


class TopicService:
    def __init__(self, repository: TopicRepository):
        self.repository = repository

    def create_topic(self, payload: TopicCreate):
        topic = Topic(subject_id=payload.subject_id, title=payload.title, sequence_order=payload.sequence_order)
        return self.repository.create(topic)

    def list_topics(self):
        return self.repository.list_all()

    def get_topic(self, topic_id: int):
        return self.repository.get_by_id(topic_id)

    def update_topic(self, topic_id: int, payload: TopicUpdate):
        topic = self.repository.get_by_id(topic_id)
        if not topic:
            return None
        if payload.subject_id is not None:
            topic.subject_id = payload.subject_id
        if payload.title is not None:
            topic.title = payload.title
        if payload.sequence_order is not None:
            topic.sequence_order = payload.sequence_order
        return self.repository.update(topic)

    def delete_topic(self, topic_id: int):
        topic = self.repository.get_by_id(topic_id)
        if not topic:
            return False
        self.repository.delete(topic)
        return True
