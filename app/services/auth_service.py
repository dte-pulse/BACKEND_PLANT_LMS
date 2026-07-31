import secrets
from app.core.security import create_access_token, verify_password, get_password_hash as hash_password
from app.repositories.user_repository import UserRepository


class AuthService:
    def __init__(self, user_repository: UserRepository):
        self.user_repository = user_repository

    def login(self, employee_code: str, password: str):
        user = self.user_repository.get_by_employee_code(employee_code)
        if not user or not verify_password(password, user.hashed_password):
            return None
        token = create_access_token(subject=str(user.id))

        # Store session in Redis
        from app.core.redis import redis_client
        from app.core.config import settings
        session_key = f"session:{token}"
        try:
            redis_client.setex(
                session_key,
                settings.access_token_expire_minutes * 60,
                str(user.id)
            )
        except Exception as e:
            # If we cannot create the session in Redis, we must abort login.
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="Session service unavailable")

        return {
            'access_token': token,
            'token_type': 'bearer',
            'role': user.role.value,
            'user_id': user.id,
            'full_name': user.full_name,
        }


    def send_reset_otp(self, email: str) -> bool:
        """Generate OTP, store it, and dispatch via email."""
        user = self.user_repository.get_by_email(email)
        if not user:
            return False  # Don't reveal if email exists
        otp = str(secrets.randbelow(900000) + 100000)  # 6-digit OTP
        self.user_repository.store_reset_otp(user.id, otp)
        # Send OTP email — dispatched async if Celery is available, else direct
        try:
            from app.tasks.email_tasks import send_otp_task
            send_otp_task.delay(email, otp, user.full_name)
        except Exception:
            # Celery unavailable — send synchronously
            from app.services.email_service import get_email_service
            get_email_service().send_otp(email, otp, user.full_name)
        return True

    def reset_password(self, email: str, otp: str, new_password: str) -> bool:
        """Verify OTP and update password."""
        user = self.user_repository.get_by_email(email)
        if not user:
            return False
        if not self.user_repository.verify_reset_otp(user.id, otp):
            return False
        new_hash = hash_password(new_password)
        self.user_repository.update_password(user.id, new_hash)
        self.user_repository.clear_reset_otp(user.id)
        return True

    def verify_reset_otp(self, email: str, otp: str) -> bool:
        """Verify OTP without consuming it so the UI can validate before reset."""
        user = self.user_repository.get_by_email(email)
        if not user:
            return False
        return self.user_repository.verify_reset_otp(user.id, otp)
