import structlog
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog

logger = structlog.get_logger()

def log_audit_event(
    db: Session,
    event: str,
    user_id: int | None = None,
    employee_code: str | None = None,
    ip_address: str | None = None,
    details: str | None = None
):
    try:
        # DB write
        audit_log = AuditLog(
            user_id=user_id,
            employee_code=employee_code,
            event=event,
            ip_address=ip_address,
            details=details
        )
        db.add(audit_log)
        db.commit()
    except Exception as e:
        logger.error("failed_to_write_audit_log_to_db", error=str(e))
    
    # Structured logging write
    logger.info(
        "audit_event_logged",
        audit_event=event,
        user_id=user_id,
        employee_code=employee_code,
        ip_address=ip_address,
        details=details
    )
