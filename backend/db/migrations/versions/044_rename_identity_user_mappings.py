"""Rename Slack user mappings table and add identity source.

Revision ID: 044
Revises: 043
Create Date: 2026-02-10
"""
from alembic import op
import sqlalchemy as sa


revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("slack_user_mappings", "user_mappings_for_identity")

    op.alter_column(
        "user_mappings_for_identity",
        "slack_user_id",
        new_column_name="external_userid",
        existing_type=sa.String(length=100),
        existing_nullable=True,
    )
    op.alter_column(
        "user_mappings_for_identity",
        "slack_email",
        new_column_name="external_email",
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )

    op.add_column(
        "user_mappings_for_identity",
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=True,
            server_default="revtops_unknown",
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE user_mappings_for_identity
            SET source = 'slack'
            WHERE source IS NULL
            """
        )
    )

    op.alter_column(
        "user_mappings_for_identity",
        "source",
        existing_type=sa.String(length=50),
        nullable=False,
        server_default="revtops_unknown",
    )
    op.create_index(
        "ix_slack_user_mappings_org_source",
        "user_mappings_for_identity",
        ["organization_id", "source"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_slack_user_mappings_org_source",
        table_name="user_mappings_for_identity",
    )

    op.drop_column("user_mappings_for_identity", "source")

    op.alter_column(
        "user_mappings_for_identity",
        "external_email",
        new_column_name="slack_email",
        existing_type=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "user_mappings_for_identity",
        "external_userid",
        new_column_name="slack_user_id",
        existing_type=sa.String(length=100),
        existing_nullable=True,
    )

    op.rename_table("user_mappings_for_identity", "slack_user_mappings")
