"""Add root_conversation_id to conversations for change-session scoping across workflow trees.

Revision ID: 039
Revises: 038
Create Date: 2026-02-05

When a parent workflow delegates to child workflows (run_workflow/loop_over), all CRM
changes from the tree should be grouped into one change session for review. Root
conversation ID identifies the top-level run so we can attach all changes to one session.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column(
            "root_conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("conversations", "root_conversation_id")
