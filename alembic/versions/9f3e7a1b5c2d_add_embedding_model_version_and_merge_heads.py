"""add embedding_model_version to documents + merge heads

Revision ID: 9f3e7a1b5c2d
Revises: 002_parent_child_chunking, a1b2c3d4e5f6
Create Date: 2026-08-11

Merges the two migration heads (parent/child chunking branch and the pgvector
embedding branch) and adds ``documents.embedding_model_version`` so the
retrieval layer can detect stale / hash-fallback vectors (E-2 fix).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f3e7a1b5c2d'
down_revision: Union[str, Sequence[str], None] = ('002_parent_child_chunking', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'documents',
        sa.Column('embedding_model_version', sa.String(length=100), nullable=True),
    )
    # I-7: ingestion-status heartbeat timestamp used by stale-job recovery.
    op.add_column(
        'documents',
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill for existing rows so the new NOT-NULL semantics below are safe.
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE documents SET updated_at = created_at WHERE updated_at IS NULL"))


def downgrade() -> None:
    op.drop_column('documents', 'updated_at')
    op.drop_column('documents', 'embedding_model_version')
