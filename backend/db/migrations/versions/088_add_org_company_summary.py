"""Add company_summary to organizations for onboarding research.

Revision ID: 088_add_org_company_summary
Revises: 087_user_fk_on_delete_cascade
Create Date: 2026-03-03

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "088_add_org_company_summary"
down_revision: Union[str, None] = "087_user_fk_on_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("company_summary", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("organizations", "company_summary")
