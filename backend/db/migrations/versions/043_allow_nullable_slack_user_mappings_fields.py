"""Allow nullable Slack user mapping fields and store RevTops email.

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
    op.add_column(
        "slack_user_mappings",
        sa.Column("revtops_email", sa.String(length=255), nullable=True),
    )
    op.alter_column(
        "slack_user_mappings",
        "user_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "slack_user_mappings",
        "slack_user_id",
        existing_type=sa.String(length=100),
        nullable=True,
    )
    op.create_index(
        "ix_slack_user_mappings_org_slack_email",
        "slack_user_mappings",
        ["organization_id", "slack_email"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_slack_user_mappings_org_slack_email",
        table_name="slack_user_mappings",
    )
    op.alter_column(
        "slack_user_mappings",
        "slack_user_id",
        existing_type=sa.String(length=100),
        nullable=False,
    )
    op.alter_column(
        "slack_user_mappings",
        "user_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.drop_column("slack_user_mappings", "revtops_email")
