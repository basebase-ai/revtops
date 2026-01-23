"""Add roles array to users

Revision ID: 006_add_user_roles
Revises: 005_add_avatar_url
Create Date: 2026-01-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = '006_add_user_roles'
down_revision: Union[str, None] = '005_add_avatar_url'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('roles', JSONB, nullable=False, server_default='[]')
    )


def downgrade() -> None:
    op.drop_column('users', 'roles')
