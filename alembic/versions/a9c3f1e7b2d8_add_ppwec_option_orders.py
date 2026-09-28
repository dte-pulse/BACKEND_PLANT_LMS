"""ppwec assessment option orders — §16 answer-order randomization

Revision ID: a9c3f1e7b2d8
Revises: f7a2c9e1b4d6
Create Date: 2026-09-26 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9c3f1e7b2d8'
down_revision: Union[str, Sequence[str], None] = 'f7a2c9e1b4d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ppwec_assessment_attempts',
        sa.Column('option_orders', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ppwec_assessment_attempts', 'option_orders')
