"""Rename Slack mappings table to generic identity mappings and add source.

Revision ID: 045
Revises: 044
Create Date: 2026-02-10
"""

from alembic import op
import sqlalchemy as sa

revision = "045"
down_revision = "044"
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
            nullable=False,
            server_default="revtops_unknown",
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE user_mappings_for_identity
            SET source = 'slack'
            WHERE source = 'revtops_unknown'
            """
        )
    )

    op.drop_index("ix_slack_user_mappings_user", table_name="user_mappings_for_identity")
    op.drop_index("ix_slack_user_mappings_org", table_name="user_mappings_for_identity")
    op.drop_index("ix_slack_user_mappings_org_user", table_name="user_mappings_for_identity")
    op.drop_index(
        "ix_slack_user_mappings_org_slack_user",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "ix_slack_user_mappings_org_slack_email",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "uq_slack_user_mappings_org_user_slack_user",
        table_name="user_mappings_for_identity",
    )

    op.create_index(
        "ix_user_mappings_for_identity_user",
        "user_mappings_for_identity",
        ["user_id"],
    )
    op.create_index(
        "ix_user_mappings_for_identity_org",
        "user_mappings_for_identity",
        ["organization_id"],
    )
    op.create_index(
        "ix_user_mappings_for_identity_org_user",
        "user_mappings_for_identity",
        ["organization_id", "user_id"],
    )
    op.create_index(
        "ix_user_mappings_for_identity_org_external_user",
        "user_mappings_for_identity",
        ["organization_id", "external_userid"],
    )
    op.create_index(
        "ix_user_mappings_for_identity_org_external_email",
        "user_mappings_for_identity",
        ["organization_id", "external_email"],
    )
    op.create_index(
        "uq_user_mappings_for_identity_org_user_external_user",
        "user_mappings_for_identity",
        ["organization_id", "user_id", "external_userid"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_mappings_for_identity_org_user_external_user",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "ix_user_mappings_for_identity_org_external_email",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "ix_user_mappings_for_identity_org_external_user",
        table_name="user_mappings_for_identity",
    )
    op.drop_index(
        "ix_user_mappings_for_identity_org_user",
        table_name="user_mappings_for_identity",
    )
    op.drop_index("ix_user_mappings_for_identity_org", table_name="user_mappings_for_identity")
    op.drop_index("ix_user_mappings_for_identity_user", table_name="user_mappings_for_identity")

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

    op.create_index(
        "uq_slack_user_mappings_org_user_slack_user",
        "user_mappings_for_identity",
        ["organization_id", "user_id", "slack_user_id"],
        unique=True,
    )
    op.create_index(
        "ix_slack_user_mappings_org_slack_email",
        "user_mappings_for_identity",
        ["organization_id", "slack_email"],
    )
    op.create_index(
        "ix_slack_user_mappings_org_slack_user",
        "user_mappings_for_identity",
        ["organization_id", "slack_user_id"],
    )
    op.create_index(
        "ix_slack_user_mappings_org_user",
        "user_mappings_for_identity",
        ["organization_id", "user_id"],
    )
    op.create_index(
        "ix_slack_user_mappings_org",
        "user_mappings_for_identity",
        ["organization_id"],
    )
    op.create_index(
        "ix_slack_user_mappings_user",
        "user_mappings_for_identity",
        ["user_id"],
    )

    op.drop_column("user_mappings_for_identity", "source")
    op.rename_table("user_mappings_for_identity", "slack_user_mappings")
