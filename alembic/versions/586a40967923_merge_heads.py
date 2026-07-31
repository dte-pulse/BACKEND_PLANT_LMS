"""Merge heads

Revision ID: 586a40967923
Revises: 8a9b7c6d5e4f, a406522b4e41
Create Date: 2026-07-20 15:28:21.431683

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '586a40967923'
down_revision: Union[str, None] = ('8a9b7c6d5e4f', 'a406522b4e41')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
