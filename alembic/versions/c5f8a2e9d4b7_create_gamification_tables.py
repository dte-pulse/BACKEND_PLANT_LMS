"""gamification coin ledger + user streaks

Revision ID: c5f8a2e9d4b7
Revises: a9c3f1e7b2d8
Create Date: 2026-09-28 10:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5f8a2e9d4b7'
down_revision: Union[str, Sequence[str], None] = 'a9c3f1e7b2d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'coin_ledger',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('activity_type', sa.String(length=50), nullable=False),
        sa.Column('points', sa.Integer(), nullable=False),
        sa.Column('module_id', sa.Integer(), nullable=True),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('reference_type', sa.String(length=50), nullable=True),
        sa.Column('reference_id', sa.String(length=255), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['module_id'], ['ppwec_modules.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_coin_ledger_id'), 'coin_ledger', ['id'], unique=False)
    op.create_index(op.f('ix_coin_ledger_user_id'), 'coin_ledger', ['user_id'], unique=False)
    # Dedup guard (app checks first; index makes the race window harmless).
    op.create_index('uq_coin_dedup', 'coin_ledger',
                    ['user_id', 'activity_type', 'reference_type', 'reference_id'],
                    unique=True)

    op.create_table(
        'user_streaks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('current_streak', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('longest_streak', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_coins', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_active_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('week_activity', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id'),
    )
    op.create_index(op.f('ix_user_streaks_id'), 'user_streaks', ['id'], unique=False)
    op.create_index(op.f('ix_user_streaks_user_id'), 'user_streaks', ['user_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_user_streaks_user_id'), table_name='user_streaks')
    op.drop_index(op.f('ix_user_streaks_id'), table_name='user_streaks')
    op.drop_table('user_streaks')
    op.drop_index('uq_coin_dedup', table_name='coin_ledger')
    op.drop_index(op.f('ix_coin_ledger_user_id'), table_name='coin_ledger')
    op.drop_index(op.f('ix_coin_ledger_id'), table_name='coin_ledger')
    op.drop_table('coin_ledger')
