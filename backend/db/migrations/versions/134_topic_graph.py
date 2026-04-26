"""134_topic_graph

Revision ID: 134_topic_graph
Revises: 133_org_members_self_edit
Create Date: 2026-04-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "134_topic_graph"
down_revision = "133_org_members_self_edit"
branch_labels = None
depends_on = None

assert len(revision) <= 32
assert not isinstance(down_revision, str) or len(down_revision) <= 32


def upgrade() -> None:
    op.create_table(
        "topic_graph_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("graph_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
        sa.Column("graph_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "graph_date", name="uq_topic_graph_org_date"),
    )
    op.create_index("ix_topic_graph_org_date", "topic_graph_snapshots", ["organization_id", "graph_date"], unique=False)
    op.create_index("ix_topic_graph_graph_date", "topic_graph_snapshots", ["graph_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_topic_graph_graph_date", table_name="topic_graph_snapshots")
    op.drop_index("ix_topic_graph_org_date", table_name="topic_graph_snapshots")
    op.drop_table("topic_graph_snapshots")
