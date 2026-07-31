"""parent child chunking

Revision ID: 002_parent_child_chunking
Revises: 076cfe04b66f
Create Date: 2026-07-22 16:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '002_parent_child_chunking'
down_revision: Union[str, None] = '076cfe04b66f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # parent_chunks
    op.create_table(
        'parent_chunks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('topic_id', sa.Integer(), nullable=False),
        sa.Column('subject_id', sa.Integer(), nullable=False),
        sa.Column('section_index', sa.Integer(), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('embedding', sa.JSON(), nullable=True),
        sa.Column('page_start', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('page_end', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('token_count', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
        sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ),
        sa.ForeignKeyConstraint(['topic_id'], ['topics.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_parent_chunks_document_id'), 'parent_chunks', ['document_id'], unique=False)
    op.create_index(op.f('ix_parent_chunks_id'), 'parent_chunks', ['id'], unique=False)
    
    # chunks
    op.add_column('chunks', sa.Column('parent_chunk_id', sa.Integer(), nullable=True))
    op.add_column('chunks', sa.Column('child_index', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('chunks', sa.Column('chunk_type', sa.String(length=20), nullable=False, server_default='child'))
    op.create_index(op.f('ix_chunks_parent_chunk_id'), 'chunks', ['parent_chunk_id'], unique=False)
    op.create_foreign_key('fk_chunks_parent_chunk_id', 'chunks', 'parent_chunks', ['parent_chunk_id'], ['id'])
    
    # child_chunk_attempts
    op.create_table(
        'child_chunk_attempts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('child_chunk_id', sa.Integer(), nullable=False),
        sa.Column('parent_chunk_id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('attempt_number', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('difficulty_shown', sa.String(length=20), nullable=False, server_default='easy'),
        sa.Column('question_text', sa.Text(), nullable=True),
        sa.Column('options_data', sa.JSON(), nullable=True),
        sa.Column('selected_option', sa.String(length=5), nullable=True),
        sa.Column('correct_option', sa.String(length=5), nullable=True),
        sa.Column('is_correct', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('explanation_shown', sa.Text(), nullable=True),
        sa.Column('re_explanation_shown', sa.Text(), nullable=True),
        sa.Column('time_taken_seconds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('mcq_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['child_chunk_id'], ['chunks.id'], ),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
        sa.ForeignKeyConstraint(['parent_chunk_id'], ['parent_chunks.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['mcq_id'], ['mcq_bank.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_child_chunk_attempts_child_chunk_id'), 'child_chunk_attempts', ['child_chunk_id'], unique=False)
    op.create_index(op.f('ix_child_chunk_attempts_document_id'), 'child_chunk_attempts', ['document_id'], unique=False)
    op.create_index(op.f('ix_child_chunk_attempts_id'), 'child_chunk_attempts', ['id'], unique=False)
    op.create_index(op.f('ix_child_chunk_attempts_parent_chunk_id'), 'child_chunk_attempts', ['parent_chunk_id'], unique=False)
    op.create_index(op.f('ix_child_chunk_attempts_user_id'), 'child_chunk_attempts', ['user_id'], unique=False)
    
    # parent_chunk_progress
    op.create_table(
        'parent_chunk_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('parent_chunk_id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=False),
        sa.Column('children_total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('children_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('knowledge_score', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('is_completed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('needs_review', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
        sa.ForeignKeyConstraint(['parent_chunk_id'], ['parent_chunks.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'parent_chunk_id', name='uq_user_parent_chunk')
    )
    op.create_index(op.f('ix_parent_chunk_progress_id'), 'parent_chunk_progress', ['id'], unique=False)


def downgrade() -> None:
    # parent_chunk_progress
    op.drop_index(op.f('ix_parent_chunk_progress_id'), table_name='parent_chunk_progress')
    op.drop_table('parent_chunk_progress')
    
    # child_chunk_attempts
    op.drop_index(op.f('ix_child_chunk_attempts_user_id'), table_name='child_chunk_attempts')
    op.drop_index(op.f('ix_child_chunk_attempts_parent_chunk_id'), table_name='child_chunk_attempts')
    op.drop_index(op.f('ix_child_chunk_attempts_id'), table_name='child_chunk_attempts')
    op.drop_index(op.f('ix_child_chunk_attempts_document_id'), table_name='child_chunk_attempts')
    op.drop_index(op.f('ix_child_chunk_attempts_child_chunk_id'), table_name='child_chunk_attempts')
    op.drop_table('child_chunk_attempts')
    
    # chunks
    op.drop_constraint('fk_chunks_parent_chunk_id', 'chunks', type_='foreignkey')
    op.drop_index(op.f('ix_chunks_parent_chunk_id'), table_name='chunks')
    op.drop_column('chunks', 'chunk_type')
    op.drop_column('chunks', 'child_index')
    op.drop_column('chunks', 'parent_chunk_id')
    
    # parent_chunks
    op.drop_index(op.f('ix_parent_chunks_id'), table_name='parent_chunks')
    op.drop_index(op.f('ix_parent_chunks_document_id'), table_name='parent_chunks')
    op.drop_table('parent_chunks')
