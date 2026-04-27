"""Add channel memory scope columns.

Revision ID: 135_chan_mem_scope
Revises: 134_topic_graph
Create Date: 2026-04-25
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "135_chan_mem_scope"
down_revision: Union[str, Sequence[str], None] = "134_topic_graph"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

assert len(revision) <= 32
assert not isinstance(down_revision, str) or len(down_revision) <= 32


def upgrade() -> None:
    op.add_column("memories", sa.Column("scope_type", sa.String(length=30), nullable=True))
    op.add_column("memories", sa.Column("scope_source", sa.String(length=30), nullable=True))
    op.add_column("memories", sa.Column("scope_channel_id", sa.String(length=255), nullable=True))

    op.alter_column("memories", "entity_id", existing_type=sa.UUID(), nullable=True)

    op.create_index(
        "ix_memories_scope_lookup",
        "memories",
        ["organization_id", "scope_type", "scope_source", "scope_channel_id", "category"],
        unique=False,
    )
    op.create_index(
        "ux_memories_channel_category",
        "memories",
        ["organization_id", "scope_type", "scope_source", "scope_channel_id", "category"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_memories_channel_category", table_name="memories")
    op.drop_index("ix_memories_scope_lookup", table_name="memories")

    # Channel-scoped rows created by this migration can have entity_id=NULL.
    # Remove them before restoring the original NOT NULL constraint.
    op.execute(sa.text("DELETE FROM memories WHERE scope_type IS NOT NULL"))

    op.alter_column("memories", "entity_id", existing_type=sa.UUID(), nullable=False)

    op.drop_column("memories", "scope_channel_id")
    op.drop_column("memories", "scope_source")
    op.drop_column("memories", "scope_type")
