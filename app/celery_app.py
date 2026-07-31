"""
Consolidated Celery application — single source of truth.
All tasks import from here. Workers start with:
  celery -A app.celery_app worker --loglevel=info
"""
import ssl
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    'pulse_lms',
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        'app.tasks.email_tasks',
        'app.tasks.document_tasks',
        'app.tasks.notification_tasks',
        'app.tasks.reminder_tasks',
        'app.tasks.report_tasks',
    ],
)

# SSL for Aiven Valkey / Redis Cloud
if settings.celery_broker_url and settings.celery_broker_url.startswith('rediss://'):
    celery_app.conf.broker_use_ssl = {'ssl_cert_reqs': ssl.CERT_NONE}
    celery_app.conf.redis_backend_use_ssl = {'ssl_cert_reqs': ssl.CERT_NONE}

celery_app.conf.update(
    task_default_queue='pulse_lms',
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        'send-overdue-reminders-daily': {
            'task': 'app.tasks.reminder_tasks.send_overdue_reminders',
            'schedule': 86400,  # every 24h
        },
        'check-upcoming-deadlines-daily': {
            'task': 'app.tasks.report_tasks.check_upcoming_deadlines',
            'schedule': 86400,  # every 24h
        },
        'send-weekly-compliance-snapshot': {
            'task': 'app.tasks.report_tasks.generate_compliance_snapshot',
            'schedule': 604800,  # every 7d
        },
    },
)
