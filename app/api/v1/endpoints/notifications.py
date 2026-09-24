"""
Phase 6 — Notifications endpoint with SSE streaming support.

VULN-009: the long-lived JWT is never placed in the URL. The client first calls
POST /notifications/stream-ticket (authenticated via Authorization header) to
obtain a short-lived, single-use ticket; EventSource then connects with
?ticket=... which is consumed on first use.
"""
import asyncio
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_user_service, resolve_user_from_token
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.schemas.notification import NotificationCreate, NotificationRead
from app.services.user_service import UserService
from app.services.notification_service import NotificationService

router = APIRouter(prefix='/notifications', tags=['notifications'])

STREAM_TICKET_TTL_SECONDS = 60  # one-time, expires fast if unused


def _ticket_key(ticket: str) -> str:
    return f'sse:ticket:{ticket}'


def get_notification_service(db: Session = Depends(get_db)):
    return NotificationService(db)


@router.get('', response_model=list[NotificationRead])
def list_notifications(
    current_user: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
):
    return service.get_user_notifications(current_user.id)


@router.get('/unread-count')
def get_unread_count(
    current_user: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
):
    count = service.get_unread_count(current_user.id)
    return {'unread_count': count}


@router.post('/{notification_id}/read', status_code=status.HTTP_204_NO_CONTENT)
def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
):
    success = service.mark_as_read(notification_id, current_user.id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Notification not found')
    return None


@router.post('/mark-all-read', status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(
    current_user: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
):
    service.mark_all_read(current_user.id)
    return None


@router.post('/stream-ticket')
def create_stream_ticket(current_user: User = Depends(get_current_user)):
    """Issue a single-use, 60-second ticket for the SSE stream (VULN-009).

    EventSource cannot send Authorization headers, so instead of exposing the
    long-lived JWT in the query string, the client exchanges it (header auth)
    for an opaque one-time ticket that only unlocks the stream endpoint.
    """
    ticket = secrets.token_urlsafe(32)
    try:
        from app.core.redis import redis_client
        redis_client.setex(
            _ticket_key(ticket),
            STREAM_TICKET_TTL_SECONDS,
            str(current_user.id),
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Stream ticket service unavailable',
        )
    return {
        'ticket': ticket,
        'expires_in': STREAM_TICKET_TTL_SECONDS,
    }


@router.get('/stream')
async def notification_stream(
    request: Request,
    user_service: UserService = Depends(get_user_service),
):
    """
    Server-Sent Events stream for real-time notifications.
    The client keeps this connection open; new notifications are pushed as they arrive.
    Poll interval: 10 seconds. Each event is a JSON payload.

    Auth: single-use ?ticket=... from POST /notifications/stream-ticket.
    """
    ticket = request.query_params.get('ticket')
    user_id: int | None = None
    if ticket:
        try:
            from app.core.redis import redis_client
            key = _ticket_key(ticket)
            raw = redis_client.get(key)
            if raw is not None:
                # Consume immediately — single use.
                redis_client.delete(key)
                user_id = int(raw)
        except Exception:
            user_id = None

    if user_id is None:
        # Back-compat: Authorization header (used when EventSource is not relied
        # upon, e.g. fetch-based stream readers). No query-string tokens.
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            try:
                user = resolve_user_from_token(auth_header.split(' ', 1)[1], user_service)
                user_id = user.id
            except HTTPException:
                user_id = None

    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing or invalid stream ticket')

    current_user_id = user_id

    async def event_generator():
        from app.db.session import SessionLocal
        
        # 1. Get initial unread count
        db_local = SessionLocal()
        try:
            service = NotificationService(db_local)
            last_count = service.get_unread_count(current_user_id)
        except Exception:
            last_count = 0
        finally:
            db_local.close()

        # Send initial state
        yield f"data: {json.dumps({'unread_count': last_count, 'event': 'init'})}\n\n"

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            await asyncio.sleep(10)  # Poll every 10 seconds

            # 2. Re-fetch in a fresh DB session context
            db_local = SessionLocal()
            try:
                service = NotificationService(db_local)
                current_count = service.get_unread_count(current_user_id)
                if current_count != last_count:
                    notifications = service.get_user_notifications(current_user.id)
                    unread = [n for n in notifications if not n.is_read]
                    payload = {
                        'event': 'new_notification',
                        'unread_count': current_count,
                        'notifications': [
                            {
                                'id': n.id,
                                'title': n.title,
                                'message': n.message,
                                'type': n.type,
                            }
                            for n in unread[:5]  # Send up to 5 most recent unread
                        ],
                    }
                    last_count = current_count
                    yield f"data: {json.dumps(payload)}\n\n"
                else:
                    # Keep-alive heartbeat
                    yield f"data: {json.dumps({'event': 'ping'})}\n\n"
            except Exception:
                break
            finally:
                db_local.close()

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )

