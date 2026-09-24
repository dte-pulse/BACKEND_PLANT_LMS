"""add structure_type to documents (P2 #6)

Revision ID: e6f9a4b5c8d2
Revises: d5e8f9a3b7c1
Create Date: 2026-09-23
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e6f9a4b5c8d2'
down_revision: str = 'd5e8f9a3b7c1'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'documents',
        sa.Column('structure_type', sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('documents', 'structure_type')
