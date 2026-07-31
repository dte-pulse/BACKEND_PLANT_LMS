"""add training evidence table

Revision ID: 8a9b7c6d5e4f
Revises: f1b2c3d4e5f6
Create Date: 2026-07-20 19:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a9b7c6d5e4f'
down_revision: Union[str, None] = 'f1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'training_evidence',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('assignment_id', sa.Integer(), nullable=False),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
        sa.Column('label', sa.String(length=255), nullable=True),
        sa.Column('file_name', sa.String(length=255), nullable=False),
        sa.Column('stored_name', sa.String(length=255), nullable=False),
        sa.Column('file_type', sa.String(length=50), nullable=True),
        sa.Column('file_url', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['assignment_id'], ['training_assignments.id']),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stored_name'),
    )
    op.create_index(op.f('ix_training_evidence_id'), 'training_evidence', ['id'], unique=False)
    op.create_index(op.f('ix_training_evidence_assignment_id'), 'training_evidence', ['assignment_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_training_evidence_assignment_id'), table_name='training_evidence')
    op.drop_index(op.f('ix_training_evidence_id'), table_name='training_evidence')
    op.drop_table('training_evidence')
