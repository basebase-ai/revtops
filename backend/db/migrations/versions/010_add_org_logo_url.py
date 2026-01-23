"""Add logo_url to organizations

Revision ID: 010_add_org_logo_url
Revises: 009_assign_orphan_integrations
Create Date: 2026-01-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '010_add_org_logo_url'
down_revision: Union[str, None] = '009_assign_orphan_integrations'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'organizations',
        sa.Column('logo_url', sa.String(512), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('organizations', 'logo_url')
