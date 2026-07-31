from sqlalchemy.orm import Session
from app.models.notification import Notification
from app.repositories.notification_repository import NotificationRepository
from app.schemas.notification import NotificationCreate


class NotificationService:
    def __init__(self, db: Session):
        self.db = db
        self.repository = NotificationRepository(db)

    def get_user_notifications(self, user_id: int):
        return self.repository.list_by_user(user_id)

    def get_unread_count(self, user_id: int) -> int:
        return self.repository.count_unread(user_id)

    def create_notification(self, payload: NotificationCreate):
        notification = Notification(
            user_id=payload.user_id,
            title=payload.title,
            message=payload.message,
            type=payload.type,
            is_read=False,
        )
        return self.repository.create(notification)

    def publish(self, user_id: int, title: str, message: str, notif_type: str = 'info'):
        """Convenience method to create a notification from anywhere in the codebase."""
        notif = Notification(
            user_id=user_id,
            title=title,
            message=message,
            type=notif_type,
            is_read=False,
        )
        created = self.repository.create(notif)
        
        # Trigger email delivery asynchronously via SES Celery task
        try:
            from app.tasks.report_tasks import send_ses_email_task
            send_ses_email_task.delay(user_id, title, message)
        except Exception:
            pass
            
        return created

    def mark_as_read(self, notification_id: int, user_id: int) -> bool:
        return self.repository.mark_as_read(notification_id, user_id)

    def mark_all_read(self, user_id: int):
        return self.repository.mark_all_read(user_id)

    # ─── Event trigger helpers ────────────────────────────────────────────────

    def notify_document_ready(self, admin_user_ids: list[int], document_code: str):
        for uid in admin_user_ids:
            self.publish(uid, 'Document Ready', f'Document {document_code} has been ingested and is ready for review.', 'success')

    def notify_nq_status(self, user_id: int, topic_title: str):
        self.publish(user_id, 'Assessment Not Qualified', f'You did not qualify in "{topic_title}". Retraining has been scheduled within 30 days.', 'warning')

    def notify_training_due(self, user_id: int, training_type: str, due_date: str):
        self.publish(user_id, 'Training Due', f'Your {training_type} training is due by {due_date}.', 'info')

    def notify_sop_updated(self, user_ids: list[int], sop_code: str):
        for uid in user_ids:
            self.publish(uid, 'SOP Updated', f'SOP {sop_code} has been updated. Retraining is required.', 'warning')

    def notify_weak_topic(self, user_id: int, topic_title: str):
        self.publish(user_id, 'Weak Topic Detected', f'You have a weak area in "{topic_title}". Focus revision recommended.', 'warning')

    def notify_calendar_alert(self, user_id: int, training_title: str, date: str):
        self.publish(user_id, 'Training Scheduled', f'{training_title} is scheduled on {date}.', 'info')
