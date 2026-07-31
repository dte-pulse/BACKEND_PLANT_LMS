from sqlalchemy.orm import Session
from app.models.user_progress import UserProgress
from app.repositories.progress_repository import ProgressRepository
from app.schemas.progress import ProgressUpdate

class ProgressService:
    def __init__(self, db: Session):
        self.repository = ProgressRepository(db)

    def get_user_progress(self, user_id: int, document_id: int):
        return self.repository.get_user_progress(user_id, document_id)

    def list_user_progress(self, user_id: int):
        return self.repository.list_by_user(user_id)

    def update_progress(self, user_id: int, payload: ProgressUpdate):
        from app.core.redis import redis_client
        from app.tasks.report_tasks import sync_progress_to_db
        
        redis_key = f"user:{user_id}:progress:{payload.document_id}"
        
        # Get existing accumulated time from Redis first, fallback to DB
        existing_time = redis_client.hget(redis_key, "time_spent_seconds")
        if existing_time is not None:
            total_time = int(existing_time) + payload.time_spent_seconds
        else:
            db_progress = self.repository.get_user_progress(user_id, payload.document_id)
            total_time = (db_progress.time_spent_seconds if db_progress else 0) + payload.time_spent_seconds

        # Update Redis hash
        redis_client.hset(redis_key, mapping={
            "topic_id": str(payload.topic_id),
            "current_chunk_id": str(payload.current_chunk_id or ""),
            "current_page": str(payload.current_page),
            "completion_percentage": str(payload.completion_percentage),
            "time_spent_seconds": str(total_time)
        })
        
        # Trigger async sync to PostgreSQL
        sync_progress_to_db.delay(user_id, payload.document_id)
        
        progress = self.repository.get_user_progress(user_id, payload.document_id)
        if not progress:
            progress = UserProgress(
                user_id=user_id,
                document_id=payload.document_id,
                topic_id=payload.topic_id,
                current_chunk_id=payload.current_chunk_id,
                current_page=payload.current_page,
                completion_percentage=payload.completion_percentage,
                time_spent_seconds=total_time
            )
        else:
            progress.topic_id = payload.topic_id
            progress.current_chunk_id = payload.current_chunk_id
            progress.current_page = payload.current_page
            progress.completion_percentage = payload.completion_percentage
            progress.time_spent_seconds = total_time
            
        return progress

