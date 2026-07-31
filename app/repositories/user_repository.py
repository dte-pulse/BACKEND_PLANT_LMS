import json
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.models.user import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db
        # In-memory OTP store (replace with Redis TTL in production)
        # Format: {user_id: {"otp": str, "expires": datetime}}
        # We'll use a DB column approach for simplicity
        self._otp_store: dict = {}

    def get(self, user_id: int):
        return self.db.query(User).filter(User.id == user_id).first()

    def get_by_employee_code(self, employee_code: str):
        return self.db.query(User).filter(User.employee_code == employee_code).first()

    def get_by_email(self, email: str):
        return self.db.query(User).filter(User.email == email).first()

    def list_all(self, *, department: str | None = None, role: str | None = None):
        query = self.db.query(User)
        if department:
            query = query.filter(User.department == department)
        if role:
            query = query.filter(User.role == role)
        return query.order_by(User.id.desc()).all()

    def list_by_department(self, department: str):
        return self.db.query(User).filter(User.department == department).all()

    def create(self, user: User):
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update(self, user: User, **kwargs):
        for k, v in kwargs.items():
            setattr(user, k, v)
        self.db.commit()
        self.db.refresh(user)
        return user

    def deactivate(self, user_id: int) -> bool:
        user = self.get(user_id)
        if not user:
            return False
        user.is_active = False
        self.db.commit()
        return True

    def activate(self, user_id: int) -> bool:
        user = self.get(user_id)
        if not user:
            return False
        user.is_active = True
        self.db.commit()
        return True


    # ─── OTP helpers (in-memory TTL; swap with Redis for production) ─────────

    def store_reset_otp(self, user_id: int, otp: str):
        self._otp_store[user_id] = {
            'otp': otp,
            'expires': datetime.now(timezone.utc) + timedelta(minutes=15),
        }

    def verify_reset_otp(self, user_id: int, otp: str) -> bool:
        entry = self._otp_store.get(user_id)
        if not entry:
            return False
        if datetime.now(timezone.utc) > entry['expires']:
            self._otp_store.pop(user_id, None)
            return False
        return entry['otp'] == otp

    def clear_reset_otp(self, user_id: int):
        self._otp_store.pop(user_id, None)

    def update_password(self, user_id: int, hashed_password: str):
        user = self.get(user_id)
        if user:
            user.hashed_password = hashed_password
            self.db.commit()
