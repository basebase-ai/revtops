"""Rename Slack user mappings table and add source metadata.

Revision ID: 044
Revises: 043
Create Date: 2026-02-11
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
        nullable=True,
    )
    op.alter_column(
        "user_mappings_for_identity",
        "slack_email",
        new_column_name="external_email",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.add_column(
        "user_mappings_for_identity",
        sa.Column(
            "source",
            sa.String(length=50),
            nullable=False,
            server_default=sa.text("'slack'"),
        ),
    )
    op.drop_index(
        "uq_slack_user_mappings_org_user_slack_user",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "ix_slack_user_mappings_org_slack_user",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "ix_slack_user_mappings_org_user",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "ix_slack_user_mappings_org_slack_email",
        table_name="user_mappings_for_identity",
    )
    op.create_index(
        "uq_identity_mappings_org_user_external_user_source",
        "user_mappings_for_identity",
        ["organization_id", "user_id", "external_userid", "source"],
        unique=True,
    )
    op.create_index(
        "ix_identity_mappings_org_external_user_source",
        "user_mappings_for_identity",
        ["organization_id", "external_userid", "source"],
    )
    op.create_index(
        "ix_identity_mappings_org_user",
        "user_mappings_for_identity",
        ["organization_id", "user_id"],
    )
    op.create_index(
        "ix_identity_mappings_org_external_email_source",
        "user_mappings_for_identity",
        ["organization_id", "external_email", "source"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_identity_mappings_org_external_email_source",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "ix_identity_mappings_org_user",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "ix_identity_mappings_org_external_user_source",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "uq_identity_mappings_org_user_external_user_source",
        table_name="user_mappings_for_identity",
    )
    op.drop_column("user_mappings_for_identity", "source")
    op.alter_column(
        "user_mappings_for_identity",
        "external_userid",
        new_column_name="slack_user_id",
        existing_type=sa.String(length=100),
        nullable=True,
    )
    op.alter_column(
        "user_mappings_for_identity",
        "external_email",
        new_column_name="slack_email",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.rename_table("user_mappings_for_identity", "slack_user_mappings")
    op.create_index(
        "uq_slack_user_mappings_org_user_slack_user",
        "slack_user_mappings",
        ["organization_id", "user_id", "slack_user_id"],
        unique=True,
    )
    op.create_index(
        "ix_slack_user_mappings_org_slack_user",
        "slack_user_mappings",
        ["organization_id", "slack_user_id"],
    )
    op.create_index(
        "ix_slack_user_mappings_org_user",
        "slack_user_mappings",
        ["organization_id", "user_id"],
    )
    op.create_index(
        "ix_slack_user_mappings_org_slack_email",
        "slack_user_mappings",
        ["organization_id", "slack_email"],
    )
