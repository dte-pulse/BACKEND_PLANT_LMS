from app.celery_app import celery_app


@celery_app.task(name='app.tasks.send_notification')
def send_notification(user_id: int, message: str):
    return {'user_id': user_id, 'message': message, 'status': 'queued'}
