from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_auth_service, get_current_user, oauth2_scheme
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserRead
from app.services.auth_service import AuthService
from app.utils.audit import log_audit_event
from app.utils.rate_limit import (
    LoginRateLimited,
    check_login_allowed,
    clear_failed_logins,
    record_failed_login,
)

router = APIRouter(prefix='/auth', tags=['auth'])


@router.post('/login', response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service)
):
    ip_address = request.client.host if request.client else None

    # VULN-006: throttle before touching the DB — account lockout + per-IP budget.
    try:
        check_login_allowed(payload.employee_code, ip_address)
    except LoginRateLimited as rl:
        log_audit_event(
            db=db,
            event='login_rate_limited',
            employee_code=payload.employee_code,
            ip_address=ip_address,
            details=rl.reason
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={'detail': rl.reason},
            headers={'Retry-After': str(rl.retry_after_seconds)},
        )

    result = auth_service.login(payload.employee_code, payload.password)
    if not result:
        record_failed_login(payload.employee_code, ip_address)
        log_audit_event(
            db=db,
            event='login_failed',
            employee_code=payload.employee_code,
            ip_address=ip_address,
            details='Invalid credentials'
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid credentials')

    clear_failed_logins(payload.employee_code)
    log_audit_event(
        db=db,
        event='login_success',            user_id=result['user_id'],
            employee_code=payload.employee_code,
            ip_address=ip_address,
            details=f"User logged in with role: {result['role']}"
    )

    # Gamification: daily-login coins (first login per UTC day only — the
    # dedup key is the date, so repeats are no-ops).
    try:
        from app.services.gamification_service import GamificationService
        GamificationService(db).notify_event(result['user_id'], 'daily_login')
        db.commit()
    except Exception:  # noqa: BLE001 — rewards must never break login
        db.rollback()

    return result



@router.get('/me', response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the currently authenticated user's profile."""
    return current_user


@router.post('/forgot-password')
def forgot_password(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service)
):
    """Send OTP reset email. Returns 200 always to avoid email enumeration."""
    email = payload.get('email', '')
    auth_service.send_reset_otp(email)
    
    log_audit_event(
        db=db,
        event='password_reset_request',
        ip_address=request.client.host if request.client else None,
        details=f"Password reset OTP requested for email: {email}"
    )
    return {'message': 'If an account with that email exists, an OTP has been sent.'}


@router.post('/reset-password')
def reset_password(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service)
):
    """Verify OTP and update password."""
    email = payload.get('email', '')
    otp = payload.get('otp', '')
    new_password = payload.get('new_password', '')
    if not all([email, otp, new_password]):
        raise HTTPException(status_code=400, detail='email, otp, and new_password are required')
    success = auth_service.reset_password(email, otp, new_password)
    if not success:
        log_audit_event(
            db=db,
            event='password_reset_failed',
            ip_address=request.client.host if request.client else None,
            details=f"Failed password reset attempt for email: {email} (invalid or expired OTP)"
        )
        raise HTTPException(status_code=400, detail='Invalid or expired OTP')
    
    log_audit_event(
        db=db,
        event='password_reset_success',
        ip_address=request.client.host if request.client else None,
        details=f"Successful password reset for email: {email}"
    )
    return {'message': 'Password reset successful'}


@router.post('/verify-otp')
def verify_otp(
    payload: dict,
    auth_service: AuthService = Depends(get_auth_service)
):
    """Validate the OTP before allowing the reset form to proceed."""
    email = payload.get('email', '')
    otp = payload.get('otp', '')
    if not all([email, otp]):
        raise HTTPException(status_code=400, detail='email and otp are required')
    if not auth_service.verify_reset_otp(email, otp):
        raise HTTPException(status_code=400, detail='Invalid or expired OTP')
    return {'message': 'OTP verified successfully'}


@router.post('/logout')
def logout(
    request: Request,
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Invalidate the session in Redis and log the event."""
    from app.core.redis import redis_client
    session_key = f"session:{token}"
    try:
        redis_client.delete(session_key)
    except Exception as e:
        raise HTTPException(status_code=503, detail="Logout failed due to session service unavailability")

    log_audit_event(
        db=db,
        event='logout',
        user_id=current_user.id,
        employee_code=current_user.employee_code,
        ip_address=request.client.host if request.client else None,
        details='User logged out'
    )
    return {'message': 'Logged out successfully'}

