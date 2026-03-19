"""Add workstream_snapshots table for caching cluster results.

Revision ID: 112_add_workstream_snapshots
Revises: 111_add_conversation_embeddings
Create Date: 2026-03-19

Caches HDBSCAN+UMAP+labels result per org and time window. stale_since set when
a conversation embedding is updated so API can recompute on next request.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "112_add_workstream_snapshots"
down_revision: Union[str, None] = "111_add_conversation_embeddings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workstream_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data", JSONB, nullable=False),
    )
    op.create_index(
        "ix_workstream_snapshots_org_window",
        "workstream_snapshots",
        ["organization_id", "window_hours"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_workstream_snapshots_org_window", table_name="workstream_snapshots")
    op.drop_table("workstream_snapshots")
