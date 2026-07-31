"""expand training assignment workflow fields

Revision ID: c4b8a6f0d219
Revises: 6e986bd02602
Create Date: 2026-07-20 15:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4b8a6f0d219'
down_revision: Union[str, None] = '6e986bd02602'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('training_assignments', sa.Column('assigned_by_id', sa.Integer(), nullable=True))
    op.add_column('training_assignments', sa.Column('notes', sa.Text(), nullable=True))
    op.add_column('training_assignments', sa.Column('requested_reason', sa.Text(), nullable=True))
    op.add_column('training_assignments', sa.Column('external_provider', sa.String(length=255), nullable=True))
    op.add_column('training_assignments', sa.Column('external_venue', sa.String(length=255), nullable=True))
    op.add_column('training_assignments', sa.Column('external_duration_hours', sa.Integer(), nullable=True))
    op.add_column('training_assignments', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        'fk_training_assignments_assigned_by_id_users',
        'training_assignments',
        'users',
        ['assigned_by_id'],
        ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_training_assignments_assigned_by_id_users', 'training_assignments', type_='foreignkey')
    op.drop_column('training_assignments', 'completed_at')
    op.drop_column('training_assignments', 'external_duration_hours')
    op.drop_column('training_assignments', 'external_venue')
    op.drop_column('training_assignments', 'external_provider')
    op.drop_column('training_assignments', 'requested_reason')
    op.drop_column('training_assignments', 'notes')
    op.drop_column('training_assignments', 'assigned_by_id')
