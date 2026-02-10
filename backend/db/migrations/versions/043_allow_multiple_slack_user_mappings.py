"""Allow multiple Slack user mappings per RevTops user and vice versa.

Revision ID: 043
Revises: 042
Create Date: 2026-02-10
"""
from alembic import op
import sqlalchemy as sa

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("uq_slack_user_mappings_org_slack_user", table_name="slack_user_mappings")
    op.create_index(
        "ix_slack_user_mappings_org_slack_user",
        "slack_user_mappings",
        ["organization_id", "slack_user_id"],
    )
    op.create_index(
        "uq_slack_user_mappings_org_user_slack_user",
        "slack_user_mappings",
        ["organization_id", "user_id", "slack_user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_slack_user_mappings_org_user_slack_user",
        table_name="slack_user_mappings",
    )
    op.drop_index("ix_slack_user_mappings_org_slack_user", table_name="slack_user_mappings")
    op.create_index(
        "uq_slack_user_mappings_org_slack_user",
        "slack_user_mappings",
        ["organization_id", "slack_user_id"],
        unique=True,
    )
