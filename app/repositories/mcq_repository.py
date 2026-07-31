from sqlalchemy.orm import Session

from app.models.mcq import MCQBank


class MCQRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_many(self, mcqs: list[MCQBank]):
        self.db.add_all(mcqs)
        self.db.commit()
        return mcqs

    def count_by_document(self, document_id: int):
        return self.db.query(MCQBank).filter(MCQBank.document_id == document_id).count()

    def get_by_document(self, document_id: int) -> list[MCQBank]:
        return self.db.query(MCQBank).filter(MCQBank.document_id == document_id).all()

    def get_by_id(self, mcq_id: int) -> MCQBank | None:
        return self.db.query(MCQBank).filter(MCQBank.id == mcq_id).first()

    def update(self, mcq: MCQBank, **kwargs) -> MCQBank:
        for key, value in kwargs.items():
            if hasattr(mcq, key):
                setattr(mcq, key, value)
        self.db.commit()
        self.db.refresh(mcq)
        return mcq

    def delete(self, mcq_id: int) -> bool:
        mcq = self.get_by_id(mcq_id)
        if mcq:
            self.db.delete(mcq)
            self.db.commit()
            return True
        return False
