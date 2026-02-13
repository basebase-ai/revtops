"""Add Slack user mappings + sender fields on chat messages.

Revision ID: 042
Revises: 041
Create Date: 2026-02-10
"""
from alembic import op
import sqlalchemy as sa

revision = "042"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "slack_user_mappings",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slack_user_id", sa.String(length=100), nullable=False),
        sa.Column("slack_email", sa.String(length=255), nullable=True),
        sa.Column("match_source", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index(
        "uq_slack_user_mappings_org_slack_user",
        "slack_user_mappings",
        ["organization_id", "slack_user_id"],
        unique=True,
    )
    op.create_index(
        "ix_slack_user_mappings_org_user",
        "slack_user_mappings",
        ["organization_id", "user_id"],
    )
    op.create_index(
        "ix_slack_user_mappings_org",
        "slack_user_mappings",
        ["organization_id"],
    )
    op.create_index(
        "ix_slack_user_mappings_user",
        "slack_user_mappings",
        ["user_id"],
    )

    op.add_column(
        "chat_messages",
        sa.Column("source_user_id", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column("source_user_email", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_chat_messages_source_user_id",
        "chat_messages",
        ["source_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_source_user_id", table_name="chat_messages")
    op.drop_column("chat_messages", "source_user_email")
    op.drop_column("chat_messages", "source_user_id")

    op.drop_index("ix_slack_user_mappings_user", table_name="slack_user_mappings")
    op.drop_index("ix_slack_user_mappings_org", table_name="slack_user_mappings")
    op.drop_index("ix_slack_user_mappings_org_user", table_name="slack_user_mappings")
    op.drop_index("uq_slack_user_mappings_org_slack_user", table_name="slack_user_mappings")
    op.drop_table("slack_user_mappings")
