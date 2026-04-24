"""Create campfires and join tables.

Revision ID: 135_campfires
Revises: 134_content_groups
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "135_campfires"
down_revision: Union[str, Sequence[str], None] = "134_content_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

assert len(revision) <= 32
if isinstance(down_revision, str):
    assert len(down_revision) <= 32


def upgrade() -> None:
    op.create_table(
        "campfires",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_campfires_org_archived", "campfires", ["organization_id", "is_archived"])

    op.create_table(
        "campfire_content_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("campfire_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("campfires.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("campfire_id", "content_group_id", name="uq_campfire_content_group"),
    )

    op.create_table(
        "campfire_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("campfire_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("campfires.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL", onupdate="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("campfire_id", "conversation_id", name="uq_campfire_conversation"),
    )

    op.execute("ALTER TABLE campfires ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE campfire_content_groups ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE campfire_conversations ENABLE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY campfires_org_isolation ON campfires
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id')::uuid)
        WITH CHECK (organization_id = current_setting('app.current_org_id')::uuid)
        """
    )

    op.execute(
        """
        CREATE POLICY campfire_content_groups_org_isolation ON campfire_content_groups
        FOR ALL
        USING (
            EXISTS (
                SELECT 1 FROM campfires
                WHERE campfires.id = campfire_content_groups.campfire_id
                  AND campfires.organization_id = current_setting('app.current_org_id')::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM campfires
                WHERE campfires.id = campfire_content_groups.campfire_id
                  AND campfires.organization_id = current_setting('app.current_org_id')::uuid
            )
        )
        """
    )

    op.execute(
        """
        CREATE POLICY campfire_conversations_org_isolation ON campfire_conversations
        FOR ALL
        USING (
            EXISTS (
                SELECT 1 FROM campfires
                WHERE campfires.id = campfire_conversations.campfire_id
                  AND campfires.organization_id = current_setting('app.current_org_id')::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM campfires
                WHERE campfires.id = campfire_conversations.campfire_id
                  AND campfires.organization_id = current_setting('app.current_org_id')::uuid
            )
        )
        """
    )

    op.execute("GRANT ALL ON campfires TO revtops_app")
    op.execute("GRANT ALL ON campfire_content_groups TO revtops_app")
    op.execute("GRANT ALL ON campfire_conversations TO revtops_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS campfire_conversations_org_isolation ON campfire_conversations")
    op.execute("DROP POLICY IF EXISTS campfire_content_groups_org_isolation ON campfire_content_groups")
    op.execute("DROP POLICY IF EXISTS campfires_org_isolation ON campfires")

    op.drop_table("campfire_conversations")
    op.drop_table("campfire_content_groups")
    op.drop_index("ix_campfires_org_archived", table_name="campfires")
    op.drop_table("campfires")
