"""allow null parent_chunk_id on child_chunk_attempts

Revision ID: b4d8e3f2c1a9
Revises: a7c9d2e4f5a6
Create Date: 2026-08-11

Legacy flat chunks (no parent section) produce attempts with
parent_chunk_id=None; the column was NOT NULL, so answering a question on
such a chunk failed with a NotNullViolation. Making it nullable — NULL rows
are excluded from parent-mastery queries, which is the correct semantic for
chunks without a parent section.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b4d8e3f2c1a9'
down_revision: Union[str, Sequence[str], None] = 'a7c9d2e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('child_chunk_attempts', 'parent_chunk_id', nullable=True)


def downgrade() -> None:
    op.alter_column('child_chunk_attempts', 'parent_chunk_id', nullable=False)
