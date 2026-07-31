"""
Document ingestion Celery task.

Features:
  - Exponential-backoff retry (3 attempts, 60 / 120 / 180 s countdowns).
  - Sets document status to 'failed' on permanent failure so the admin UI
    can surface the error instead of leaving it stuck in a processing state.
  - Notifies all admin users via NotificationService once ingestion completes.
"""
import logging

from app.db.session import SessionLocal
from app.services.ingestion_service import IngestionService
from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name='app.tasks.process_document',
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def process_document(self, document_id: int):
    """Process a document through the full ingestion pipeline.

    Retry up to 3 times with escalating back-off on transient failures.
    On permanent failure the document status is set to 'failed' and all
    admin users receive an error notification.
    """
    db = SessionLocal()
    try:
        service = IngestionService(db)
        result = service.process_document(document_id)

        # ── Admin notification on success ─────────────────────────────────
        try:
            from app.models.user import User, UserRole
            from app.services.notification_service import NotificationService

            admin_ids = [
                u.id
                for u in db.query(User.id)
                .filter(User.role == UserRole.admin, User.is_active == True)  # noqa: E712
                .all()
            ]
            if admin_ids:
                doc = service.document_repository.get_by_id(document_id)
                doc_code = (doc.title or f'ID-{document_id}') if doc else f'ID-{document_id}'
                notif_svc = NotificationService(db)
                notif_svc.notify_document_ready(admin_ids, doc_code)
                logger.info(
                    'Ingestion complete — notified %d admin(s) for document %d',
                    len(admin_ids),
                    document_id,
                )
        except Exception as notif_exc:  # never let notification failure kill the task
            logger.warning('Admin notification after ingestion failed: %s', notif_exc)

        return result

    except Exception as exc:
        attempt = self.request.retries + 1
        logger.error(
            'Ingestion failed for document %d (attempt %d/%d): %s',
            document_id,
            attempt,
            self.max_retries + 1,
            exc,
            exc_info=True,
        )

        if self.request.retries < self.max_retries:
            # Exponential back-off: 60 s, 120 s, 180 s
            countdown = 60 * (self.request.retries + 1)
            logger.info(
                'Scheduling retry %d for document %d in %d s',
                attempt,
                document_id,
                countdown,
            )
            raise self.retry(exc=exc, countdown=countdown)

        # ── Permanent failure — mark document as failed ───────────────────
        try:
            from app.repositories.document_repository import DocumentRepository
            from app.models.user import User, UserRole
            from app.services.notification_service import NotificationService

            doc_repo = DocumentRepository(db)
            doc = doc_repo.get_by_id(document_id)
            if doc:
                doc_repo.update(doc, status='failed')
                logger.error('Document %d marked as failed after %d attempts.', document_id, attempt)

            # Notify admins of the failure
            admin_ids = [
                u.id
                for u in db.query(User.id)
                .filter(User.role == UserRole.admin, User.is_active == True)  # noqa: E712
                .all()
            ]
            if admin_ids:
                doc_code = (doc.title or f'ID-{document_id}') if doc else f'ID-{document_id}'
                notif_svc = NotificationService(db)
                for uid in admin_ids:
                    notif_svc.publish(
                        uid,
                        'Document Ingestion Failed',
                        f'Ingestion for "{doc_code}" failed after {attempt} attempts. '
                        f'Error: {exc}',
                        'error',
                    )
        except Exception as cleanup_exc:
            logger.error('Failed to mark document as failed: %s', cleanup_exc)

        raise  # re-raise so Celery records the task as FAILURE

    finally:
        db.close()
