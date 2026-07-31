"""
Report tasks — weekly compliance snapshots stored as notifications for admins.
"""
import logging
from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name='app.tasks.report_tasks.generate_compliance_snapshot')
def generate_compliance_snapshot():
    """
    Weekly task: compute global readiness + per-dept compliance,
    then notify all admin users with a summary.
    """
    from app.db.session import SessionLocal
    from app.services.report_service import ReportService
    from app.services.notification_service import NotificationService
    from app.models.user import User, UserRole

    db = SessionLocal()
    try:
        report_svc = ReportService(db)
        notif_svc = NotificationService(db)

        readiness = report_svc.get_global_readiness()
        compliance = report_svc.get_compliance_report()
        overdue = report_svc.get_overdue_report()

        # Notify all admins
        admins = db.query(User).filter(User.role == UserRole.admin, User.is_active == True).all()
        summary = (
            f"Weekly Compliance Snapshot: Global readiness {readiness['readiness_score']}%. "
            f"{readiness['completed']}/{readiness['total_assignments']} assignments complete. "
            f"{len(overdue)} overdue assignments."
        )
        for admin in admins:
            notif_svc.publish(
                admin.id,
                'Weekly Compliance Report',
                summary,
                notif_type='info',
            )

        logger.info(f'Compliance snapshot sent to {len(admins)} admins')
        return {'admins_notified': len(admins), 'readiness': readiness['readiness_score']}
    finally:
        db.close()


@celery_app.task(name='app.tasks.report_tasks.log_token_usage')
def log_token_usage(user_id: int, operation: str, prompt_tokens: int, completion_tokens: int, cost_usd: float, cache_hit: bool = False, latency_ms: int = 0):
    """Persist one LLM call to the TokenUsageLog table."""
    from app.db.session import SessionLocal
    from app.models.token_usage_log import TokenUsageLog

    db = SessionLocal()
    try:
        log = TokenUsageLog(
            user_id=user_id,
            operation=operation,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=cost_usd,
            cache_hit=cache_hit,
            latency_ms=latency_ms,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f'Token logging failed: {e}')
        db.rollback()
    finally:
        db.close()


@celery_app.task(name='app.tasks.report_tasks.sync_progress_to_db')
def sync_progress_to_db(user_id: int, document_id: int):
    """Sync trainee progress from Redis to PostgreSQL db."""
    from datetime import datetime, timezone
    from app.db.session import SessionLocal
    from app.core.redis import redis_client
    from app.models.user_progress import UserProgress
    from app.models.training import TrainingAssignment

    db = SessionLocal()
    try:
        redis_key = f"user:{user_id}:progress:{document_id}"
        progress_data = redis_client.hgetall(redis_key)
        if not progress_data:
            logger.info(f"No Redis progress found for user {user_id}, doc {document_id}")
            return

        topic_id = int(progress_data.get("topic_id", 0))
        current_chunk_id = progress_data.get("current_chunk_id")
        current_chunk_id = int(current_chunk_id) if current_chunk_id else None
        current_page = int(progress_data.get("current_page", 1))
        completion_percentage = float(progress_data.get("completion_percentage", 0.0))
        time_spent_seconds = int(progress_data.get("time_spent_seconds", 0))

        progress = db.query(UserProgress).filter(
            UserProgress.user_id == user_id,
            UserProgress.document_id == document_id
        ).first()

        if progress:
            progress.topic_id = topic_id
            progress.current_chunk_id = current_chunk_id
            progress.current_page = current_page
            progress.completion_percentage = completion_percentage
            progress.time_spent_seconds = time_spent_seconds
            progress.last_accessed_at = datetime.now(timezone.utc)
        else:
            progress = UserProgress(
                user_id=user_id,
                document_id=document_id,
                topic_id=topic_id,
                current_chunk_id=current_chunk_id,
                current_page=current_page,
                completion_percentage=completion_percentage,
                time_spent_seconds=time_spent_seconds,
                last_accessed_at=datetime.now(timezone.utc)
            )
            db.add(progress)
        db.commit()

        if completion_percentage >= 100.0:
            assignment = db.query(TrainingAssignment).filter(
                TrainingAssignment.user_id == user_id,
                TrainingAssignment.document_id == document_id,
                TrainingAssignment.status != 'completed'
            ).first()
            if assignment:
                assignment.status = 'completed'
                db.commit()
        
        logger.info(f"Successfully synced progress for user {user_id}, doc {document_id} to DB")
    except Exception as e:
        logger.error(f"Failed to sync progress to DB: {e}")
        db.rollback()
    finally:
        db.close()


@celery_app.task(name='app.tasks.report_tasks.send_ses_email_task')
def send_ses_email_task(user_id: int, title: str, message: str):
    """Send email notification via AWS SES."""
    import boto3
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.core.config import settings

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.email:
            logger.warning(f"User {user_id} not found or email is empty")
            return

        # Initialize boto3 SES client
        ses_client = boto3.client(
            'ses',
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region
        )

        sender_email = "no-reply@pulselms.com"
        
        if settings.aws_access_key_id == 'change-me' or settings.aws_secret_access_key == 'change-me':
            logger.info(f"[Mock SES Email] To: {user.email} | Subject: {title} | Body: {message}")
            return

        response = ses_client.send_email(
            Source=sender_email,
            Destination={'ToAddresses': [user.email]},
            Message={
                'Subject': {'Data': f"[Pulse LMS] {title}"},
                'Body': {'Text': {'Data': message}}
            }
        )
        logger.info(f"SES email sent to {user.email}, message ID: {response.get('MessageId')}")
    except Exception as e:
        logger.error(f"Failed to send email via SES: {e}. Falling back to mock delivery.")
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if user:
                logger.info(f"[Mock SES Fallback] To: {user.email} | Subject: {title} | Body: {message}")
        except Exception:
            pass
    finally:
        db.close()


@celery_app.task(name='app.tasks.report_tasks.check_upcoming_deadlines')
def check_upcoming_deadlines():
    """Daily check for training assignments due in 3 days or today, and send notifications."""
    from datetime import datetime, timedelta, timezone
    from app.db.session import SessionLocal
    from app.models.training import TrainingAssignment
    from app.services.notification_service import NotificationService

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        three_days_later = now + timedelta(days=3)

        upcoming_assignments = db.query(TrainingAssignment).filter(
            TrainingAssignment.status != 'completed',
            TrainingAssignment.due_date >= now,
            TrainingAssignment.due_date <= three_days_later
        ).all()

        notif_svc = NotificationService(db)
        count = 0
        for ta in upcoming_assignments:
            due_str = ta.due_date.strftime('%Y-%m-%d')
            notif_svc.notify_training_due(ta.user_id, ta.training_type, due_str)
            count += 1
        logger.info(f"Checked upcoming deadlines. Sent {count} notifications.")
    except Exception as e:
        logger.error(f"Error checking upcoming deadlines: {e}")
    finally:
        db.close()


