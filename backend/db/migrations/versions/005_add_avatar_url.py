"""Add avatar_url to users

Revision ID: 005_add_avatar_url
Revises: 004_add_waitlist
Create Date: 2026-01-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '005_add_avatar_url'
down_revision: Union[str, None] = '004_add_waitlist'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('avatar_url', sa.String(512), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('users', 'avatar_url')
