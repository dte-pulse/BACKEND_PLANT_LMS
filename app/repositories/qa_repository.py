from sqlalchemy.orm import Session

from app.models.user_qa_session import UserQaSession


class QaRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, qa_id: int):
        return self.db.query(UserQaSession).filter(UserQaSession.id == qa_id).first()

    def create(self, qa_session: UserQaSession):
        self.db.add(qa_session)
        self.db.commit()
        self.db.refresh(qa_session)
        return qa_session

    def update(self, qa_session: UserQaSession, **kwargs):
        for key, value in kwargs.items():
            setattr(qa_session, key, value)
        self.db.add(qa_session)
        self.db.commit()
        self.db.refresh(qa_session)
        return qa_session

    def list_by_user(self, user_id: int):
        return self.db.query(UserQaSession).filter(UserQaSession.user_id == user_id).order_by(UserQaSession.id.asc()).all()

    def list_by_user_and_document(self, user_id: int, document_id: int):
        return (
            self.db.query(UserQaSession)
            .filter(UserQaSession.user_id == user_id, UserQaSession.document_id == document_id)
            .order_by(UserQaSession.id.asc())
            .all()
        )

