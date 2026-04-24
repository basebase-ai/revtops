"""Create content_groups and conversation linkage.

Revision ID: 134_content_groups
Revises: 133_org_members_self_edit
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "134_content_groups"
down_revision: Union[str, Sequence[str], None] = "133_org_members_self_edit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

assert len(revision) <= 32
if isinstance(down_revision, str):
    assert len(down_revision) <= 32


def upgrade() -> None:
    op.create_table(
        "content_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("workspace_id", sa.String(length=100), nullable=False),
        sa.Column("external_group_id", sa.String(length=255), nullable=False),
        sa.Column("external_thread_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "organization_id",
            "platform",
            "workspace_id",
            "external_group_id",
            "external_thread_id",
            name="uq_content_groups_key",
        ),
    )
    op.create_index("ix_content_groups_org_platform_workspace", "content_groups", ["organization_id", "platform", "workspace_id"])
    op.create_index("ix_content_groups_org_platform_group", "content_groups", ["organization_id", "platform", "external_group_id"])

    op.add_column("conversations", sa.Column("content_group_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_conversations_content_group_id",
        "conversations",
        "content_groups",
        ["content_group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_conversations_content_group", "conversations", ["content_group_id"])

    op.execute("ALTER TABLE content_groups ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY content_groups_org_isolation ON content_groups
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id')::uuid)
        WITH CHECK (organization_id = current_setting('app.current_org_id')::uuid)
        """
    )
    op.execute("GRANT ALL ON content_groups TO revtops_app")


def downgrade() -> None:
    op.drop_index("ix_conversations_content_group", table_name="conversations")
    op.drop_constraint("fk_conversations_content_group_id", "conversations", type_="foreignkey")
    op.drop_column("conversations", "content_group_id")

    op.execute("DROP POLICY IF EXISTS content_groups_org_isolation ON content_groups")
    op.drop_index("ix_content_groups_org_platform_group", table_name="content_groups")
    op.drop_index("ix_content_groups_org_platform_workspace", table_name="content_groups")
    op.drop_table("content_groups")
