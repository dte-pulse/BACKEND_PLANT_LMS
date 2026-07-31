from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import ALGORITHM
from app.db.session import get_db
from app.models.user import User, UserRole
from app.repositories.document_repository import DocumentRepository
from app.repositories.subject_repository import SubjectRepository
from app.repositories.topic_repository import TopicRepository
from app.repositories.training_repository import TrainingRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.document_service import DocumentService
from app.services.ingestion_service import IngestionService
from app.services.subject_service import SubjectService
from app.services.topic_service import TopicService
from app.services.training_service import TrainingService
from app.services.user_service import UserService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_v1_prefix}/auth/login")


def get_user_service(db: Session = Depends(get_db)):
    return UserService(UserRepository(db))


def get_auth_service(db: Session = Depends(get_db)):
    return AuthService(UserRepository(db))


def get_document_service(db: Session = Depends(get_db)):
    return DocumentService(DocumentRepository(db))


def get_training_service(db: Session = Depends(get_db)):
    return TrainingService(TrainingRepository(db))


def get_subject_service(db: Session = Depends(get_db)):
    return SubjectService(SubjectRepository(db))


def get_topic_service(db: Session = Depends(get_db)):
    return TopicService(TopicRepository(db))


def get_ingestion_service(db: Session = Depends(get_db)):
    return IngestionService(db)


def resolve_user_from_token(token: str, user_service: UserService) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        user_id_str: str = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception

    # Redis session check & sliding window expiration update
    from app.core.redis import redis_client
    session_key = f"session:{token}"
    try:
        # Check if the session exists in Redis
        if redis_client.exists(session_key) == 0:
            raise credentials_exception
        # Extend the TTL (sliding window inactivity)
        redis_client.expire(session_key, settings.access_token_expire_minutes * 60)
    except HTTPException:
        raise
    except Exception:
        # If Redis is unreachable, fail securely rather than bypassing session revocation
        raise credentials_exception

    user = user_service.repository.get(user_id)
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


def get_current_user(
    token: str = Depends(oauth2_scheme),
    user_service: UserService = Depends(get_user_service)
) -> User:
    return resolve_user_from_token(token, user_service)



def require_role(roles: list[UserRole]):
    def role_checker(current_user: User = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions"
            )
        return current_user
    return role_checker
