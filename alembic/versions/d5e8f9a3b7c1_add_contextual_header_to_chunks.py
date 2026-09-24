"""add contextual_header to chunks

Upgrade 2 — Contextual Retrieval (Anthropic): each child chunk stores a short
LLM-generated context header (section title → document → position in doc) that
is prepended at embed/BM25/answer time, so chunks retrieved in isolation still
carry document-level context.

Revision ID: d5e8f9a3b7c1
Revises: b4d8e3f2c1a9
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e8f9a3b7c1'
down_revision: Union[str, Sequence[str], None] = 'b4d8e3f2c1a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chunks', sa.Column('contextual_header', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('chunks', 'contextual_header')
