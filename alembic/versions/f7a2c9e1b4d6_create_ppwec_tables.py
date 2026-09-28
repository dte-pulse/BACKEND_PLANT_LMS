"""ppwec tables — Pulse Professional Workplace Excellence Certification

Revision ID: f7a2c9e1b4d6
Revises: e6f9a4b5c8d2
Create Date: 2026-09-26 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a2c9e1b4d6'
down_revision: Union[str, Sequence[str], None] = 'e6f9a4b5c8d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ppwec_modules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_number', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('theme', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('estimated_minutes', sa.Integer(), nullable=False),
        sa.Column('passing_score', sa.Float(), nullable=False),
        sa.Column('badge_name', sa.String(length=255), nullable=True),
        sa.Column('badge_icon', sa.String(length=50), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('version', sa.String(length=30), nullable=False),
        sa.Column('content_owner', sa.String(length=255), nullable=True),
        sa.Column('is_mandatory', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module_number'),
    )
    op.create_index(op.f('ix_ppwec_modules_id'), 'ppwec_modules', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_modules_module_number'), 'ppwec_modules', ['module_number'], unique=False)

    op.create_table(
        'ppwec_screens',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('screen_number', sa.Integer(), nullable=False),
        sa.Column('section', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('on_screen_text', sa.JSON(), nullable=True),
        sa.Column('voice_over', sa.Text(), nullable=True),
        sa.Column('audio_url', sa.String(length=500), nullable=True),
        sa.Column('caption_url', sa.String(length=500), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('video_url', sa.String(length=500), nullable=True),
        sa.Column('visual_direction', sa.Text(), nullable=True),
        sa.Column('interaction_type', sa.String(length=50), nullable=False),
        sa.Column('interaction_payload', sa.JSON(), nullable=True),
        sa.Column('is_mandatory', sa.Boolean(), nullable=False),
        sa.Column('estimated_seconds', sa.Integer(), nullable=False),
        sa.Column('pulse_anchor', sa.String(length=50), nullable=True),
        sa.Column('md_philosophy', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('module_id', 'screen_number', name='uq_ppwec_screen_order'),
    )
    op.create_index(op.f('ix_ppwec_screens_id'), 'ppwec_screens', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_screens_module_id'), 'ppwec_screens', ['module_id'], unique=False)

    op.create_table(
        'ppwec_questions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('question_type', sa.String(length=50), nullable=False),
        sa.Column('case_context', sa.Text(), nullable=True),
        sa.Column('question_text', sa.Text(), nullable=False),
        sa.Column('options', sa.JSON(), nullable=False),
        sa.Column('correct_option', sa.String(length=20), nullable=False),
        sa.Column('feedback_why', sa.Text(), nullable=True),
        sa.Column('feedback_better', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ppwec_questions_id'), 'ppwec_questions', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_questions_module_id'), 'ppwec_questions', ['module_id'], unique=False)

    op.create_table(
        'ppwec_user_module_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('screens_completed', sa.JSON(), nullable=True),
        sa.Column('current_screen_id', sa.Integer(), nullable=True),
        sa.Column('completion_percentage', sa.Float(), nullable=False),
        sa.Column('time_spent_seconds', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('best_score', sa.Float(), nullable=True),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('badge_awarded', sa.Boolean(), nullable=False),
        sa.Column('points_earned', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_accessed_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id']),
        sa.ForeignKeyConstraint(['current_screen_id'], ['ppwec_screens.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'module_id', name='uq_ppwec_user_module'),
    )
    op.create_index(op.f('ix_ppwec_user_module_state_id'), 'ppwec_user_module_state', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_user_module_state_user_id'), 'ppwec_user_module_state', ['user_id'], unique=False)
    op.create_index(op.f('ix_ppwec_user_module_state_module_id'), 'ppwec_user_module_state', ['module_id'], unique=False)

    op.create_table(
        'ppwec_assessment_attempts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('question_ids', sa.JSON(), nullable=False),
        sa.Column('answers', sa.JSON(), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('passed', sa.Boolean(), nullable=True),
        sa.Column('attempt_number', sa.Integer(), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ppwec_assessment_attempts_id'), 'ppwec_assessment_attempts', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_assessment_attempts_user_id'), 'ppwec_assessment_attempts', ['user_id'], unique=False)
    op.create_index(op.f('ix_ppwec_assessment_attempts_module_id'), 'ppwec_assessment_attempts', ['module_id'], unique=False)

    op.create_table(
        'ppwec_points_ledger',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('activity_type', sa.String(length=50), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=True),
        sa.Column('reference_id', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_ppwec_points_ledger_id'), 'ppwec_points_ledger', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_points_ledger_user_id'), 'ppwec_points_ledger', ['user_id'], unique=False)

    op.create_table(
        'ppwec_badges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('icon', sa.String(length=50), nullable=True),
        sa.Column('is_final', sa.Boolean(), nullable=False),
        sa.Column('required_modules', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index(op.f('ix_ppwec_badges_id'), 'ppwec_badges', ['id'], unique=False)

    op.create_table(
        'ppwec_user_badges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('badge_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=True),
        sa.Column('awarded_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['badge_id'], ['ppwec_badges.id']),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'badge_id', name='uq_ppwec_user_badge'),
    )
    op.create_index(op.f('ix_ppwec_user_badges_id'), 'ppwec_user_badges', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_user_badges_user_id'), 'ppwec_user_badges', ['user_id'], unique=False)
    op.create_index(op.f('ix_ppwec_user_badges_badge_id'), 'ppwec_user_badges', ['badge_id'], unique=False)

    op.create_table(
        'ppwec_challenge_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('days_completed', sa.JSON(), nullable=True),
        sa.Column('points_awarded', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'module_id', name='uq_ppwec_challenge'),
    )
    op.create_index(op.f('ix_ppwec_challenge_progress_id'), 'ppwec_challenge_progress', ['id'], unique=False)
    op.create_index(op.f('ix_ppwec_challenge_progress_user_id'), 'ppwec_challenge_progress', ['user_id'], unique=False)
    op.create_index(op.f('ix_ppwec_challenge_progress_module_id'), 'ppwec_challenge_progress', ['module_id'], unique=False)


def downgrade() -> None:
    op.drop_table('ppwec_challenge_progress')
    op.drop_table('ppwec_user_badges')
    op.drop_table('ppwec_badges')
    op.drop_table('ppwec_points_ledger')
    op.drop_table('ppwec_assessment_attempts')
    op.drop_table('ppwec_user_module_state')
    op.drop_table('ppwec_questions')
    op.drop_table('ppwec_screens')
    op.drop_table('ppwec_modules')
