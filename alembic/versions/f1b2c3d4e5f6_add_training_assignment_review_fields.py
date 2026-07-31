"""add training assignment review fields

Revision ID: f1b2c3d4e5f6
Revises: c4b8a6f0d219
Create Date: 2026-07-20 17:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1b2c3d4e5f6'
down_revision: Union[str, None] = 'c4b8a6f0d219'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('training_assignments', sa.Column('approved_by_id', sa.Integer(), nullable=True))
    op.add_column('training_assignments', sa.Column('approval_notes', sa.Text(), nullable=True))
    op.add_column('training_assignments', sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        'fk_training_assignments_approved_by_id_users',
        'training_assignments',
        'users',
        ['approved_by_id'],
        ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_training_assignments_approved_by_id_users', 'training_assignments', type_='foreignkey')
    op.drop_column('training_assignments', 'approved_at')
    op.drop_column('training_assignments', 'approval_notes')
    op.drop_column('training_assignments', 'approved_by_id')
