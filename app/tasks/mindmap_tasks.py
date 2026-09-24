"""
Mind map Celery tasks (P1 #2).

Regenerating the mind map concept tree used to run the (potential) LLM call
inline in the HTTP request worker — 10–30s per call, and a couple of admins
could stall the whole API. The deterministic (numbered-document) path is cheap
and stays synchronous; the LLM path (non-numbered documents) is dispatched
here instead. Completion state is published to Redis so the endpoint that
accepted the job (or a poller) can report progress.
"""
import json
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)

# Redis status key (shared with the API endpoint): doc-scoped, short TTL.
STATUS_TTL_SECONDS = 3600  # 1h — status is transient job metadata


def status_key(document_id: int) -> str:
    return f'mindmap-regen:status:{document_id}'


def set_status(document_id: int, state: str, extra: dict | None = None) -> None:
    """Best-effort status publish; Redis outage must never fail the job."""
    try:
        from app.core.redis import redis_client
        payload = {'state': state, 'document_id': document_id, **(extra or {})}
        redis_client.setex(status_key(document_id), STATUS_TTL_SECONDS, json.dumps(payload))
    except Exception as e:
        logger.warning(f'Mind map regen status publish failed (non-critical): {e}')


@celery_app.task(
    name='app.tasks.mindmap_tasks.regenerate_mind_map_tree',
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def regenerate_mind_map_tree(self, document_id: int, user_id: int):
    """Build a fresh concept tree for a non-numbered document via the LLM.

    Numbered documents never reach this task (the endpoint handles them
    synchronously — deterministic, no model call).
    """
    from app.db.session import SessionLocal
    from app.models.document import Document
    from app.services.mind_map_service import build_regenerated_tree
    from app.repositories.document_repository import DocumentRepository

    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            set_status(document_id, 'failed', {'detail': 'Document not found'})
            return {'status': 'failed', 'detail': 'Document not found'}

        tree, used_llm = build_regenerated_tree(db, document, user_id=user_id)
        DocumentRepository(db).update(document, mind_map_json=tree)

        # The concept tree is SHARED across users — every user's cached
        # /mindmap and /structure payloads are now stale.
        try:
            from app.services.response_cache import invalidate_cached
            invalidate_cached('resp:learning:')
        except Exception as inv_exc:
            logger.warning(f'Mind map regen cache invalidation failed (non-critical): {inv_exc}')

        set_status(document_id, 'ready', {'concept_nodes_count': len(tree)})
        logger.info(f'Mind map tree regenerated for doc {document_id} by user {user_id} (llm={used_llm})')
        return {'status': 'ready', 'concept_nodes_count': len(tree)}
    except Exception as exc:
        db.rollback()
        logger.error(f'Mind map regeneration failed for doc {document_id}: {exc}')
        set_status(document_id, 'failed', {'detail': str(exc)[:300]})
        # Transient LLM/API failures are worth one or two more shots.
        raise self.retry(exc=exc)
    finally:
        db.close()
