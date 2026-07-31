"""
Reminder tasks — daily overdue notifications to trainees.
"""
import logging
from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name='app.tasks.reminder_tasks.send_overdue_reminders')
def send_overdue_reminders():
    """
    Run daily. Finds all overdue training assignments and emails each trainee.
    """
    from app.db.session import SessionLocal
    from app.services.report_service import ReportService
    from app.services.email_service import get_email_service
    from app.repositories.user_repository import UserRepository

    db = SessionLocal()
    email_svc = get_email_service()
    sent = 0
    errors = 0

    try:
        report_svc = ReportService(db)
        overdue = report_svc.get_overdue_report()

        # Group by user
        by_user: dict[int, list] = {}
        for item in overdue:
            uid = item['user_id']
            by_user.setdefault(uid, []).append(item)

        user_repo = UserRepository(db)
        for user_id, items in by_user.items():
            user = user_repo.get(user_id)
            if not user or not user.email:
                continue
            try:
                email_svc.send_overdue_reminder(user.email, user.full_name, items)
                sent += 1
            except Exception as e:
                logger.error(f'Overdue reminder failed for user {user_id}: {e}')
                errors += 1

        logger.info(f'Overdue reminders: {sent} sent, {errors} errors')
        return {'sent': sent, 'errors': errors}
    finally:
        db.close()


@celery_app.task(name='app.tasks.reminder_tasks.send_training_due_soon')
def send_training_due_soon():
    """
    Warn trainees 3 days before a training due date.
    """
    from datetime import datetime, timedelta, timezone
    from app.db.session import SessionLocal
    from app.models.training import TrainingAssignment
    from app.repositories.user_repository import UserRepository
    from app.services.email_service import get_email_service

    db = SessionLocal()
    email_svc = get_email_service()
    sent = 0

    try:
        now = datetime.now(timezone.utc)
        soon = now + timedelta(days=3)
        upcoming = db.query(TrainingAssignment).filter(
            TrainingAssignment.status != 'completed',
            TrainingAssignment.due_date.isnot(None),
            TrainingAssignment.due_date >= now,
            TrainingAssignment.due_date <= soon,
        ).all()

        user_repo = UserRepository(db)
        for a in upcoming:
            user = user_repo.get(a.user_id)
            if not user or not user.email:
                continue
            email_svc.send_training_assigned(
                user.email,
                user.full_name,
                a.training_type,
                f'Document #{a.document_id}',
                a.due_date.strftime('%d-%b-%Y') if a.due_date else '',
            )
            sent += 1

        return {'sent': sent}
    finally:
        db.close()
