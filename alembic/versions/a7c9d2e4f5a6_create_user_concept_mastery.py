"""create user_concept_mastery table

Revision ID: a7c9d2e4f5a6
Revises: 9f3e7a1b5c2d
Create Date: 2026-08-11

Adds per-user, per-concept (child chunk) mastery tracking used by the
adaptive learning agents (CurriculumAgent / WeaknessAgent / RecommenderAgent):
a recency-weighted score, a mastery band, attempt counters and an optional
LLM insight. Rolls up to the existing topic-level UserWeaknessProfile so the
NQ reports keep working.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c9d2e4f5a6'
down_revision: Union[str, Sequence[str], None] = '9f3e7a1b5c2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_concept_mastery',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('documents.id'), nullable=False, index=True),
        # Nullable: legacy flat chunks may carry topic_id=0 / parent_chunk_id=NULL.
        # NULL passes FK constraints, 0 does not — missing values are stored NULL.
        sa.Column('topic_id', sa.Integer(), sa.ForeignKey('topics.id'), nullable=True, index=True),
        sa.Column('parent_chunk_id', sa.Integer(), sa.ForeignKey('parent_chunks.id'), nullable=True, index=True),
        sa.Column('child_chunk_id', sa.Integer(), sa.ForeignKey('chunks.id'), nullable=False, index=True),
        sa.Column('score', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('mastery_level', sa.String(length=20), nullable=False, server_default='novice'),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('correct_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('consecutive_correct', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_difficulty', sa.String(length=20), nullable=False, server_default='easy'),
        sa.Column('insight', sa.Text(), nullable=True),
        sa.Column('needs_review', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'child_chunk_id', name='uq_user_child_concept'),
    )


def downgrade() -> None:
    op.drop_table('user_concept_mastery')
