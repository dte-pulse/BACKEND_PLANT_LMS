from sqlalchemy.orm import Session

from app.models.document import Document


class DocumentRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_all(self):
        return self.db.query(Document).order_by(Document.id.desc()).all()

    def get_by_id(self, document_id: int):
        return self.db.query(Document).filter(Document.id == document_id).first()

    def create(self, document: Document):
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        return document

    def update(self, document: Document, **kwargs):
        for key, value in kwargs.items():
            setattr(document, key, value)
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        return document

    def delete(self, document: Document):
        self.db.delete(document)
        self.db.commit()
