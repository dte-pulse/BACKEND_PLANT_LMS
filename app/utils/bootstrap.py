from sqlalchemy import text
from app.db.session import Base, engine
from app.models import Document, TrainingAssignment, User


def init_db():
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_latest BOOLEAN DEFAULT TRUE;"))
            conn.execute(text("ALTER TABLE parent_chunks ADD COLUMN IF NOT EXISTS stable_id VARCHAR(255);"))
            conn.execute(text("ALTER TABLE parent_chunks ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64);"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS stable_id VARCHAR(255);"))
            conn.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64);"))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Could not apply versioning migrations: {e}")

