"""Add archived_at column to apps table.

Revision ID: 080_app_archived_at
Revises: 079_org_domain_nonunique
Create Date: 2026-03-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "080_app_archived_at"
down_revision: Union[str, None] = "079_org_domain_nonunique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("apps", sa.Column("archived_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("apps", "archived_at")
