from sqlalchemy.orm import Session

from app.models.chunk import Chunk


class ChunkRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_many(self, chunks: list[Chunk]):
        self.db.add_all(chunks)
        self.db.commit()
        return chunks

    def count_by_document(self, document_id: int):
        return self.db.query(Chunk).filter(Chunk.document_id == document_id).count()

    def get_by_document(self, document_id: int) -> list[Chunk]:
        return self.db.query(Chunk).filter(Chunk.document_id == document_id).order_by(Chunk.chunk_index).all()
