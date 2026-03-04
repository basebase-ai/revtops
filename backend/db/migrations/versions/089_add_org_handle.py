"""Add handle to organizations for org-prefixed URLs.

Revision ID: 089_add_org_handle
Revises: 088_add_org_company_summary
Create Date: 2026-03-03

Human-readable, URL-safe identifier (e.g. "joinable", "cro-metrics").
Nullable for existing orgs until manually assigned.
Unique when set. Used for routes like /{handle}/artifacts/{id}.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "089_add_org_handle"
down_revision: Union[str, None] = "088_add_org_company_summary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("handle", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_organizations_handle_unique",
        "organizations",
        ["handle"],
        unique=True,
        postgresql_where=sa.text("handle IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organizations_handle_unique",
        table_name="organizations",
        postgresql_where=sa.text("handle IS NOT NULL"),
    )
    op.drop_column("organizations", "handle")
