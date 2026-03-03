"""Add website_url to organizations.

Revision ID: 086_add_org_website_url
Revises: 085_admin_org_scope
Create Date: 2026-03-03

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "086_add_org_website_url"
down_revision: Union[str, None] = "085_admin_org_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("website_url", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "website_url")
