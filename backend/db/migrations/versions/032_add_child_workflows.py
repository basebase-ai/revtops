"""Add child_workflows to workflows for composition.

Revision ID: 032
Revises: 031
Create Date: 2026-02-03

Workflows can now declare which other workflows they depend on.
At runtime, child workflow metadata (id, name, schemas) is automatically
injected into the prompt so the agent doesn't need to look them up.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers
revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add child_workflows column - array of workflow UUIDs this workflow can call
    op.add_column(
        "workflows",
        sa.Column("child_workflows", JSONB, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("workflows", "child_workflows")
