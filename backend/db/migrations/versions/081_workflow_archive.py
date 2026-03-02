"""Add archived_at to workflows.

Revision ID: 081_workflow_archive
Revises: 080_app_archived_at
Create Date: 2026-03-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "081_workflow_archive"
down_revision: Union[str, None] = "080_app_archived_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workflows", sa.Column("archived_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflows", "archived_at")
