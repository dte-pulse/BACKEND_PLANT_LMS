def send_email(to: str, subject: str, html_body: str, text_body: str = ''):
    """
    Unified email entry point. Dispatches to Celery task for async delivery.
    """
    from app.tasks.email_tasks import send_email_task
    send_email_task.delay(to, subject, html_body, text_body)
