"""Create workstreams table for persistent editable workstream labels.

Revision ID: 113_create_workstreams_table
Revises: 112_add_workstream_snapshots
Create Date: 2026-03-19

One row per workstream cluster per org+window. label_overridden prevents
AI from overwriting user-edited names on recompute.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID


revision: str = "113_create_workstreams_table"
down_revision: Union[str, None] = "112_add_workstream_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workstreams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("label_overridden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("conversation_ids", ARRAY(UUID(as_uuid=True)), nullable=False, server_default=sa.text("'{}'::uuid[]")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", ARRAY(sa.Float()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_workstreams_org_window_active",
        "workstreams",
        ["organization_id", "window_hours", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_workstreams_org_window_active", table_name="workstreams")
    op.drop_table("workstreams")
