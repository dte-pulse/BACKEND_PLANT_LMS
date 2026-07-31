from sqlalchemy.orm import Session
from app.models.notification import Notification

class NotificationRepository:
    def __init__(self, db: Session):
        self.db = db

    def count_unread(self, user_id: int):
        return self.db.query(Notification).filter(Notification.user_id == user_id, Notification.is_read == False).count()

    def create(self, notification: Notification):
        self.db.add(notification)
        self.db.commit()
        self.db.refresh(notification)
        return notification

    def mark_as_read(self, notification_id: int, user_id: int):
        notif = self.db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user_id).first()
        if notif:
            notif.is_read = True
            self.db.commit()
            self.db.refresh(notif)
            return True
        return False

    def mark_all_read(self, user_id: int):
        self.db.query(Notification).filter(
            Notification.user_id == user_id,
            Notification.is_read == False,
        ).update({'is_read': True})
        self.db.commit()

    def list_by_user(self, user_id: int):
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        return (
            self.db.query(Notification)
            .filter(Notification.user_id == user_id, Notification.created_at >= cutoff)
            .order_by(Notification.created_at.desc())
            .all()
        )
