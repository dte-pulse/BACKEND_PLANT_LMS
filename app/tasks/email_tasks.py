"""
Consolidated task modules — all import from app.celery_app (single source).
"""
import logging
from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name='app.tasks.email_tasks.send_email_task', bind=True, max_retries=3)
def send_email_task(self, to: str, subject: str, html_body: str, text_body: str = ''):
    """Generic email dispatch task — retries up to 3x on failure."""
    try:
        from app.services.email_service import get_email_service
        svc = get_email_service()
        success = svc.send(to, subject, html_body, text_body or None)
        if not success:
            raise RuntimeError(f'Email send to {to} returned False')
        return {'status': 'sent', 'to': to}
    except Exception as exc:
        logger.error(f'Email task failed (attempt {self.request.retries + 1}): {exc}')
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(name='app.tasks.email_tasks.send_otp_task')
def send_otp_task(to: str, otp: str, user_name: str = ''):
    from app.services.email_service import get_email_service
    return get_email_service().send_otp(to, otp, user_name)


@celery_app.task(name='app.tasks.email_tasks.send_training_assigned_task')
def send_training_assigned_task(to: str, user_name: str, training_type: str, doc_title: str, due_date: str = ''):
    from app.services.email_service import get_email_service
    return get_email_service().send_training_assigned(to, user_name, training_type, doc_title, due_date)


@celery_app.task(name='app.tasks.email_tasks.send_nq_alert_task')
def send_nq_alert_task(to: str, user_name: str, topic: str, deadline: str):
    from app.services.email_service import get_email_service
    return get_email_service().send_nq_alert(to, user_name, topic, deadline)
