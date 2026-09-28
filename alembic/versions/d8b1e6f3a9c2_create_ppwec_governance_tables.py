"""ppwec governance tables — pilot feedback, signoffs, design freeze, validations

Revision ID: d8b1e6f3a9c2
Revises: c5f8a2e9d4b7
Create Date: 2026-09-28 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8b1e6f3a9c2'
down_revision: Union[str, Sequence[str], None] = 'c5f8a2e9d4b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ppwec_pilot_feedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('engagement', sa.Integer(), nullable=False),
        sa.Column('relevance', sa.Integer(), nullable=False),
        sa.Column('realism', sa.Integer(), nullable=False),
        sa.Column('clarity', sa.Integer(), nullable=False),
        sa.Column('overall_rating', sa.Float(), nullable=False),
        sa.Column('comments', sa.Text(), nullable=True),
        sa.Column('function_tag', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module_id', 'user_id', name='uq_ppwec_pilot_once'),
    )
    op.create_index(op.f('ix_ppwec_pilot_feedback_id'), 'ppwec_pilot_feedback', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_pilot_feedback_module_id'), 'ppwec_pilot_feedback', ['module_id'], unique=False)
    op.create_index(op.f('ix_ppwec_pilot_feedback_user_id'), 'ppwec_pilot_feedback', ['user_id'], unique=False)

    op.create_table(
        'ppwec_review_signoffs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('stage', sa.String(length=50), nullable=False),
        sa.Column('reviewer_id', sa.Integer(), nullable=False),
        sa.Column('reviewer_name', sa.String(length=255), nullable=True),
        sa.Column('decision', sa.String(length=20), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id'], ),
        sa.ForeignKeyConstraint(['reviewer_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module_id', 'stage', 'reviewer_id', name='uq_ppwec_signoff_once'),
    )
    op.create_index(op.f('ix_ppwec_review_signoffs_id'), 'ppwec_review_signoffs', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_review_signoffs_module_id'), 'ppwec_review_signoffs', ['module_id'], unique=False)

    op.create_table(
        'ppwec_design_freeze',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('frozen_by_id', sa.Integer(), nullable=False),
        sa.Column('frozen_by_name', sa.String(length=255), nullable=True),
        sa.Column('snapshot', sa.JSON(), nullable=True),
        sa.Column('frozen_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('unfrozen_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id'], ),
        sa.ForeignKeyConstraint(['frozen_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module_id'),
    )
    op.create_index(op.f('ix_ppwec_design_freeze_id'), 'ppwec_design_freeze', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_design_freeze_module_id'), 'ppwec_design_freeze', ['module_id'], unique=True)

    op.create_table(
        'ppwec_validation_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('ok', sa.Boolean(), nullable=False),
        sa.Column('errors', sa.JSON(), nullable=True),
        sa.Column('warnings', sa.JSON(), nullable=True),
        sa.Column('ran_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id'], ),
        sa.ForeignKeyConstraint(['ran_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ppwec_validation_runs_id'), 'ppwec_validation_runs', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_validation_runs_module_id'), 'ppwec_validation_runs', ['module_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ppwec_validation_runs_module_id'), table_name='ppwec_validation_runs')
    op.drop_index(op.f('ix_ppwec_validation_runs_id'), table_name='ppwec_validation_runs')
    op.drop_table('ppwec_validation_runs')
    op.drop_index(op.f('ix_ppwec_design_freeze_module_id'), table_name='ppwec_design_freeze')
    op.drop_index(op.f('ix_ppwec_design_freeze_id'), table_name='ppwec_design_freeze')
    op.drop_table('ppwec_design_freeze')
    op.drop_index(op.f('ix_ppwec_review_signoffs_module_id'), table_name='ppwec_review_signoffs')
    op.drop_index(op.f('ix_ppwec_review_signoffs_id'), table_name='ppwec_review_signoffs')
    op.drop_table('ppwec_review_signoffs')
    op.drop_index(op.f('ix_ppwec_pilot_feedback_user_id'), table_name='ppwec_pilot_feedback')
    op.drop_index(op.f('ix_ppwec_pilot_feedback_module_id'), table_name='ppwec_pilot_feedback')
    op.drop_index(op.f('ix_ppwec_pilot_feedback_id'), table_name='ppwec_pilot_feedback')
    op.drop_table('ppwec_pilot_feedback')
