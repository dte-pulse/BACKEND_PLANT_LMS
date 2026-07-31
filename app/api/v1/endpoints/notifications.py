"""
Phase 6 — Notifications endpoint with SSE streaming support.
"""
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_user_service, resolve_user_from_token
from app.db.session import get_db
from app.models.user import User
from app.schemas.notification import NotificationCreate, NotificationRead
from app.services.user_service import UserService
from app.services.notification_service import NotificationService

router = APIRouter(prefix='/notifications', tags=['notifications'])


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


@router.get('/stream')
async def notification_stream(
    request: Request,
    db: Session = Depends(get_db),
    user_service: UserService = Depends(get_user_service),
):
    """
    Server-Sent Events stream for real-time notifications.
    The client keeps this connection open; new notifications are pushed as they arrive.
    Poll interval: 10 seconds. Each event is a JSON payload.
    """
    token = request.query_params.get('token')
    if not token:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1]
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing auth token')

    current_user = resolve_user_from_token(token, user_service)
    service = NotificationService(db)

    async def event_generator():
        last_count = service.get_unread_count(current_user.id)
        # Send initial state
        yield f"data: {json.dumps({'unread_count': last_count, 'event': 'init'})}\n\n"

        while True:
            # Check if client disconnected
            if await request.is_disconnected():
                break

            await asyncio.sleep(10)  # Poll every 10 seconds

            try:
                # Re-fetch in same DB session
                current_count = service.get_unread_count(current_user.id)
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

    return StreamingResponse(
        event_generator(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )
