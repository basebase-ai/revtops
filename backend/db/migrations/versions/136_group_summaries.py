"""Create content_group_summaries table.

Revision ID: 136_group_summaries
Revises: 135_campfires
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "136_group_summaries"
down_revision: Union[str, Sequence[str], None] = "135_campfires"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

assert len(revision) <= 32
if isinstance(down_revision, str):
    assert len(down_revision) <= 32


def upgrade() -> None:
    op.create_table(
        "content_group_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("content_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_message_external_id", sa.String(length=255), nullable=True),
        sa.Column("last_message_external_id", sa.String(length=255), nullable=True),
        sa.Column("first_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summarized_through_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=50), nullable=False, server_default=sa.text("'v1'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index(
        "ix_group_summaries_group_through_desc",
        "content_group_summaries",
        ["content_group_id", sa.text("summarized_through_at DESC")],
    )
    op.create_index(
        "ix_group_summaries_org_group_range",
        "content_group_summaries",
        ["organization_id", "content_group_id", "first_message_at", "last_message_at"],
    )

    op.execute("ALTER TABLE content_group_summaries ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY content_group_summaries_org_isolation ON content_group_summaries
        FOR ALL
        USING (organization_id = current_setting('app.current_org_id')::uuid)
        WITH CHECK (organization_id = current_setting('app.current_org_id')::uuid)
        """
    )
    op.execute("GRANT ALL ON content_group_summaries TO revtops_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS content_group_summaries_org_isolation ON content_group_summaries")
    op.drop_index("ix_group_summaries_org_group_range", table_name="content_group_summaries")
    op.drop_index("ix_group_summaries_group_through_desc", table_name="content_group_summaries")
    op.drop_table("content_group_summaries")
